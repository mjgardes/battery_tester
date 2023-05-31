import logging
log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

import sys
import tempfile
import numpy as np
from time import perf_counter, sleep
import pyvisa
from pymeasure.log import console_log
from pymeasure.display.Qt import QtWidgets
from pymeasure.display.windows import ManagedWindow
from pymeasure.experiment import Procedure, Results
from pymeasure.experiment import IntegerParameter, FloatParameter, Parameter

class RandomProcedure(Procedure):

    nominal_capacity = FloatParameter('Nominal', units='Ah', default=25.3)
    charge_rate = FloatParameter('Charge rate', units='C', default=0.6)
    discharge_rate = FloatParameter('Discharge rate', units='C', default=0.2)
    charge_voltage = FloatParameter('Charge limit', units='V', default=3.8)
    float_charge_voltage  = FloatParameter('Float voltage', units='V', default=3.6)
    float_current_cutoff = FloatParameter('Float cutoff', units='A', default=0.55)
    discharge_voltage = FloatParameter('Discharge limit', units='V', default=2)

    DATA_COLUMNS = ['Time', 'Discharge_time', 'Voltage', 'Current', 'Charge', 'Ah_V', 'SoC']

    def set(self, instrument, command, log_text):
        log.info(log_text)
        instrument.write(command)
        log.debug(instrument.read_raw())
        log.debug(instrument.read_raw())

    def fetch(self, instrument, command, log_text = None):
        if log_text != None:
            log.debug(log_text)
        result = instrument.query_ascii_values(command)[0]
        log.debug(instrument.read())
        return result

    def clear_buffer(self, instrument):
        buffer_empty = False
        while buffer_empty == False:
            try:
                log.debug(instrument.read())
            except:
                log.debug('Buffer empty')
                buffer_empty = True
            else:
                log.debug('Purged 1 line from buffer')
                log.debug('Residue in buffer')

    def startup(self):
        rm = pyvisa.ResourceManager()
        self.boss = rm.open_resource('ASRL/dev/ttyUSB_Gearmo::INSTR')
        self.boss.read_termination = '\r\n\n>' # Stupid, but it works
        self.boss.write_termination = '\r\n'
        self.boss.baud_rate = 9600

        self.set(self.boss, 'SB0', 'Set Backtalk 0')
        self.set(self.boss, 'SR', 'Setting Remote mode')
        self.set(self.boss, 'PL+40', 'Program limit to 1/4 ful scale (0x40)')
        self.set(self.boss, 'SI', 'Set current control mode')
        self.set(self.boss, 'PC0', 'Zero setpoint')

    def execute(self):
        test_start_time = perf_counter()
        time_elapsed = 0
        last_time = 0
        charge = 0.0
        last_voltage = 0.0
        timeout_seconds = 1.5 * 3600 / self.charge_rate

        if self.should_stop():
            log.warning("Skipping recharge")
        else:
            self.set(self.boss, f'PC+{self.charge_rate * self.nominal_capacity:.3f}A', f'Charging at {self.charge_rate * self.nominal_capacity:.3n}A') 

            time_elapsed = 0
            last_time = 0

            self.clear_buffer(self.boss)

            while time_elapsed < timeout_seconds * 2:
                voltage = self.fetch(self.boss, 'MV')[0]
                current = self.fetch(self.boss, 'MI')[0]

                time_elapsed = perf_counter() - test_start_time
                time_interval = time_elapsed - last_time
                delta_charge = current * time_interval / 3600
                charge += delta_charge
                last_time = time_elapsed
                delta_voltage = voltage - last_voltage
                last_voltage = voltage
                if delta_voltage == 0.0:
                    Ah_V = np.nan
                else:
                    Ah_V = delta_charge/delta_voltage

                data = {
                    'Time': time_elapsed,
                    'Discharge_time': np.nan,
                    'Voltage': voltage,
                    'Current': current,
                    'Charge': charge,
                    'Ah_V': Ah_V,
                    'SoC': np.nan
                }

                self.emit('results', data)
                log.debug("Emitting results: %s" % data)
                self.emit('progress', 100 * time_elapsed / timeout_seconds)

                if voltage >= self.charge_voltage:
                    log.info('Pack charged')
                    break

                sleep(2)
                if self.should_stop():
                    log.warning("Caught the stop flag in the procedure")
                    break
        
        if self.should_stop():
            log.warning("Skipping float charge")
        else:
            self.set(self.boss, 'SV', 'Set to voltage control mode')
            self.set(self.boss, 'PL+C0', 'Program current limit to 3/4 ful scale (0xC0)')
            self.set(self.boss, f'PC+{self.charge_voltage:.3f}', f'Float charging at {self.charge_voltage:.3n} V')

            self.clear_buffer(self.boss)

            float_start = time_elapsed
            float_seconds = 30 * 60

            while time_elapsed < float_start + float_seconds:
                voltage = self.fetch(self.boss, 'MV')[0]
                current = self.fetch(self.boss, 'MI')[0]

                time_elapsed = perf_counter() - test_start_time
                time_interval = time_elapsed - last_time
                delta_charge = current * time_interval / 3600
                charge += delta_charge
                last_time = time_elapsed
                delta_voltage = voltage - last_voltage
                last_voltage = voltage
                if delta_voltage == 0.0:
                    Ah_V = np.nan
                else:
                    Ah_V = delta_charge/delta_voltage

                data = {
                    'Time': time_elapsed,
                    'Discharge_time': np.nan,
                    'Voltage': voltage,
                    'Current': current,
                    'Charge': charge,
                    'Ah_V': Ah_V,
                    'SoC': np.nan #100 * charge / self.nominal_capacity
                }

                self.emit('results', data)
                log.debug("Emitting results: %s" % data)
                self.emit('progress', 100 * (time_elapsed-float_start) / float_seconds)

                if current <= self.float_current_cutoff:
                    log.info('Pack charged')
                    break

                if self.should_stop():
                    log.warning("Caught the stop flag in the procedure")
                    break

                sleep(2)

        if self.should_stop():
            log.warning("Skipping discharge test")
        else:
            self.set(self.boss, 'SI', 'Setting current control mode')
            self.set(self.boss, f'PC-%{self.discharge_rate * self.nominal_capacity:.3f}', f'Discharging at {self.discharge_rate * self.nominal_capacity:.3n}A')

            discharge_start = time_elapsed
            last_time = perf_counter() - test_start_time
            charge = 0.0
            
            self.clear_buffer(self.boss)

            while time_elapsed < timeout_seconds:
                voltage = self.fetch(self.boss, 'MV')[0]
                current = self.fetch(self.boss, 'MI')[0]

                time_elapsed = perf_counter() - test_start_time
                time_interval = time_elapsed - last_time
                delta_charge = current * time_interval / 3600
                charge += delta_charge
                last_time = time_elapsed
                delta_voltage = voltage - last_voltage
                if delta_voltage == 0.0:
                    Ah_V = np.nan
                else:
                    Ah_V = delta_charge/delta_voltage

                data = {
                    'Time': time_elapsed,
                    'Discharge_time': time_elapsed - discharge_start,
                    'Voltage': voltage,
                    'Current': current,
                    'Charge': charge,
                    'Ah_V': Ah_V,
                    'SoC': 100 * (1 + (charge / self.nominal_capacity))
                }

                self.emit('results', data)
                log.debug("Emitting results: %s" % data)
                self.emit('progress', 100 * time_elapsed / timeout_seconds)

                if voltage <= self.discharge_voltage:
                    log.info('Pack discharged')
                    break

                if self.should_stop():
                    log.warning("Caught the stop flag in the procedure")
                    break

                sleep(2)

        self.set(self.boss, 'SI', 'Setting current control mode')
        self.fetch(self.boss, 'PC0', 'Setting current to zero')

class MainWindow(ManagedWindow):

    def __init__(self):
        super().__init__(
            procedure_class=RandomProcedure,
            inputs=['nominal_capacity', 'charge_rate', 'discharge_rate', 'charge_voltage', 'discharge_voltage', 'float_charge_voltage', 'float_current_cutoff'],
            displays=['nominal_capacity', 'charge_rate', 'discharge_rate', 'charge_voltage', 'discharge_voltage', 'float_charge_voltage', 'float_current_cutoff'],
            x_axis='Time',
            y_axis='Voltage'
        )
        self.setWindowTitle('Battery Tester')

    def queue(self):
        filename = tempfile.mktemp()

        procedure = self.make_procedure()
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)

        self.manager.queue(experiment)

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())