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
    charge_rate = FloatParameter('Charge rate', units='C', default=0.3)
    discharge_rate = FloatParameter('Discharge rate', units='C', default=0.2)
    charge_voltage = FloatParameter('Charge limit', units='V', default=3.8)
    float_charge_voltage  = FloatParameter('Float voltage', units='V', default=3.8)
    float_current_cutoff = FloatParameter('Float cutoff', units='A', default=0.55)
    discharge_voltage = FloatParameter('Discharge limit', units='V', default=2)

    DATA_COLUMNS = ['Time', 'Discharge_time', 'Voltage', 'Current', 'Charge', 'Ah_V', 'SoC']

    def set(self, instrument, command, log_text):
        log.info(log_text)
        if instrument == self.boss:
            log.info(f'{command}\t{instrument.query(command)}')
        else:
            instrument.write(command)

    def fetch(self, instrument, command, log_text = None):
        if log_text != None:
            log.debug(log_text)
        result = instrument.query_ascii_values(command)[0]
        return result

    def startup(self):
        rm = pyvisa.ResourceManager('@py')
        self.boss = rm.open_resource('GPIB0::8::INSTR', read_termination = '\r\n', write_termination = '\r\n', query_delay = 0.01)
        self.fluke = rm.open_resource('GPIB0::1::INSTR')
        self.set(self.boss, 'SR', 'Setting Remote mode')
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
            setpoint = self.charge_rate * self.nominal_capacity
            self.set(self.boss, 'SI', 'Set current control mode')
            self.set(self.boss, 'PL-0', 'Program - limit to zero')
            voltage_limit_fs = 100 * (self.charge_voltage + 0.2) / 20
            self.set(self.boss, f'PL+%{voltage_limit_fs:.3f}', f'Program + limit to {voltage_limit_fs:.3n}% of FS')
            self.set(self.boss, f'PC+{setpoint:.3f}', f'Charging at {setpoint:.3n}')
            self.set(self.fluke, 'VDC', 'Setting Fluke to measure voltage')

            time_elapsed = 0
            last_time = 0

            while time_elapsed < timeout_seconds * 2:
                voltage = -self.fetch(self.fluke, 'VAL1?')
                current = setpoint

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
            self.set(self.fluke, 'ADC', 'Set Fluke to measure current')
            self.set(self.boss, 'SV', 'Set to voltage control mode')
            self.set(self.boss, 'PL-0', 'Program - limit to zero')
            self.set(self.boss, 'PL+80', 'Program current limit to 1/2 ful scale (0x80)')
            self.set(self.boss, f'PC+{self.charge_voltage:.3f}', f'Float charging at {self.charge_voltage:.3n} V')
            #sleep(0.1) # Time for Fluke to recombobulate

            float_start = time_elapsed
            float_seconds = 30 * 60

            while time_elapsed < float_start + float_seconds:
                voltage = self.charge_voltage
                current = self.fetch(self.fluke, 'VAL1?')

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
            setpoint = self.discharge_rate * self.nominal_capacity
            self.set(self.boss, 'SI', 'Setting current control mode')
            self.set(self.boss, 'PL-40', 'Program - limit to 1/4 full scale (0x40)')
            self.set(self.boss, 'PL+40', 'Program voltage limit to 1/4 full scale (0x40)')
            self.set(self.boss, f'PC-{setpoint:.3f}', f'Discharging at {setpoint:.3n}A')
            self.set(self.fluke, 'VDC', 'Setting Fluke to measure voltage')

            discharge_start = time_elapsed
            last_time = perf_counter() - test_start_time
            charge = 0.0

            while time_elapsed < timeout_seconds:
                voltage = -self.fetch(self.fluke, 'VAL1?')
                current = -setpoint

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
        self.set(self.boss, 'PC0', 'Setting current to zero')
        self.set(self.boss, 'SL', 'Return local control')

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
