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

    def startup(self):
        rm = pyvisa.ResourceManager()
        self.boss = rm.open_resource('ASRL/dev/ttyUSB_Gearmo::INSTR')
        self.boss.read_termination = '\r\n\n>' # Stupid, but it works
        self.boss.write_termination = '\r\n'
        self.boss.baud_rate = 9600

        log.info('Set Backtalk 0')
        self.boss.write('SB0')

        log.info('Setting Remote mode')
        self.boss.write('SR')
        log.debug(self.boss.read_raw())
        log.debug(self.boss.read_raw())

        log.info('Set current control mode')
        self.boss.write('SI')
        log.debug(self.boss.read_raw())
        log.debug(self.boss.read_raw())

        log.info('Zero setpoint')
        self.boss.write('PC0')
        log.debug(self.boss.read_raw())
        log.debug(self.boss.read_raw())

    def execute(self):
        test_start_time = perf_counter()
        time_elapsed = 0
        last_time = 0
        charge = 0.0
        last_voltage = 0.0
        charge_timeout_seconds = 1.3 * 3600 / self.charge_rate
        discharge_timeout_seconds = 1.3 * 3600 / self.discharge_rate

        if self.should_stop():
            log.warning("Skipping recharge")
        else:

            log.info('Program voltage limit to 1/4 ful scale (0x40)')
            self.boss.write('PL+40')
            log.debug(self.boss.read_raw())
            log.debug(self.boss.read_raw())

            log.info(f'Charging at {self.charge_rate * self.nominal_capacity:.3n}A')
            self.boss.write(f'PC+{self.charge_rate * self.nominal_capacity:.3f}A')
            log.debug(self.boss.read())

            time_elapsed = 0
            last_time = 0

            buffer_empty = False
            while buffer_empty == False:
                try:
                    log.debug(self.boss.read())
                except:
                    log.debug('Buffer empty')
                    buffer_empty = True
                else:
                    log.debug('Purged 1 line from buffer')
                    log.debug('Residue in buffer')

            while time_elapsed < charge_timeout_seconds:
                voltage = self.boss.query_ascii_values('MV')[0]
                log.debug(self.boss.read())
                current = self.boss.query_ascii_values('MI')[0]
                log.debug(self.boss.read())

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
                self.emit('progress', 100 * time_elapsed / charge_timeout_seconds)

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
            log.info('Set to voltage control mode')
            self.boss.write('SV')
            log.debug(self.boss.read_raw())
            log.debug(self.boss.read_raw())
            log.info('Program current limit to 3/4 ful scale (0xC0)')
            self.boss.write('PL+C0')

            log.debug(self.boss.read_raw())
            log.debug(self.boss.read_raw())
            log.info(f'Float charging at {self.charge_voltage:.3n} V')
            self.boss.write(f'PC+{self.charge_voltage:.3f}V')
            log.debug(self.boss.read())

            buffer_empty = False
            while buffer_empty == False:
                try:
                    log.debug(self.boss.read())
                except:
                    log.debug('Buffer empty')
                    buffer_empty = True
                else:
                    log.debug('Purged 1 line from buffer')
                    log.debug('Residue in buffer')

            float_start = time_elapsed
            float_seconds = 30 * 60

            while time_elapsed < float_start + float_seconds:
                voltage = self.boss.query_ascii_values('MV')[0]
                log.debug(self.boss.read())
                current = self.boss.query_ascii_values('MI')[0]
                log.debug(self.boss.read())

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

                sleep(2)
                if self.should_stop():
                    log.warning("Caught the stop flag in the procedure")
                    break
        
        if self.should_stop():
            log.warning("Skipping discharge test")
        else:
            log.info('Setting current control mode')
            self.boss.write('SI')
            log.debug(self.boss.read_raw())
            log.debug(self.boss.read_raw())

            log.info('Program limit to 1/4 ful scale (0x40)')
            self.boss.write('PL+40')
            log.debug(self.boss.read_raw())
            log.info(self.boss.read_raw())

            log.info(f'Discharging at {self.discharge_rate * self.nominal_capacity:.3n}A')
            self.boss.write(f'PC-{self.discharge_rate * self.nominal_capacity:.3f}')
            log.debug(self.boss.read_raw())
            log.debug(self.boss.read_raw())

            discharge_start = time_elapsed
            last_time = perf_counter() - test_start_time
            charge = 0.0
            
            buffer_empty = False
            while buffer_empty == False:
                try:
                    log.debug(self.boss.read())
                except:
                    log.debug('Buffer empty')
                    buffer_empty = True
                else:
                    log.debug('Purged 1 line from buffer')
                    log.debug('Residue in buffer')

            while time_elapsed - discharge_start  < discharge_timeout_seconds:
                voltage = self.boss.query_ascii_values('MV')[0]
                log.debug(self.boss.read())
                current = self.boss.query_ascii_values('MI')[0]
                log.debug(self.boss.read())

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
                self.emit('progress', 100 * (time_elapsed - discharge_start) / discharge_timeout_seconds)

                if voltage <= self.discharge_voltage:
                    log.info('Pack discharged')
                    break

                sleep(2)
                if self.should_stop():
                    log.warning("Caught the stop flag in the procedure")
                    break
        
        log.info('Setting current to zero')
        self.boss.write('SI')
        log.debug(self.boss.read_raw())
        log.debug(self.boss.read_raw())
        self.boss.write('PC0')
        log.info(self.boss.read())
        log.debug(self.boss.read_raw())

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