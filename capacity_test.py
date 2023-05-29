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

    nominal_capacity = FloatParameter('Nominal', units='Ah', default=17)
    charge_rate = FloatParameter('Charge rate', units='C', default=0.2)
    discharge_rate = FloatParameter('Discharge rate', units='C', default=0.2)
    charge_voltage = FloatParameter('Charge limit', units='V', default=4.00)
    discharge_voltage = FloatParameter('Discharge limit', units='V', default=3.65)

    DATA_COLUMNS = ['Time', 'Voltage', 'Current', 'Charge', 'Ah_V', 'SoC']

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
        log.info(self.boss.read_raw())
        log.info(self.boss.read_raw())

        log.info('Program limit to 1/4 ful scale (0x40)')
        self.boss.write('PL+40')
        log.info(self.boss.read_raw())
        log.info(self.boss.read_raw())

        log.info('Set current control mode')
        self.boss.write('SI')
        log.info(self.boss.read_raw())
        log.info(self.boss.read_raw())

        log.info('Zero setpoint')
        self.boss.write('PC0')
        log.info(self.boss.read_raw())
        log.info(self.boss.read_raw())

    def execute(self):
        log.info('Discharging at 3.4A')
        self.boss.write('PC-3.400')
        log.info(self.boss.read_raw())
        log.info(self.boss.read_raw())

        test_start_time = perf_counter()
        time_elapsed = 0
        last_time = test_start_time
        log.info("Clock time: %f" % test_start_time)
        timeout_seconds = 6*3600
        charge = 0.0
        last_voltage = 0.0

        buffer_empty = False
        while buffer_empty == False:
            try:
                log.info(self.boss.read())
            except:
                log.info('Buffer empty')
                buffer_empty = True
            else:
                log.info('Purged 1 line from buffer')
                log.info('Residue in buffer')

        while time_elapsed < timeout_seconds:
            voltage = self.boss.query_ascii_values('MV')[0]
            log.info(self.boss.read())
            current = self.boss.query_ascii_values('MI')[0]
            log.info(self.boss.read())

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
                'Voltage': voltage,
                'Current': current,
                'Charge': charge,
                'Ah_V': Ah_V,
                'SoC': np.nan
            }

            self.emit('results', data)
            log.debug("Emitting results: %s" % data)
            self.emit('progress', 100 * time_elapsed / timeout_seconds)

            if voltage <= self.discharge_voltage:
                log.info('Pack discharged')
                break

            sleep(0.5)
            if self.should_stop():
                log.warning("Caught the stop flag in the procedure")
                break
        
        charge = 0.0

        if self.should_stop():
            log.warning("Skipping recharge")
        else:

            log.info('Charging at 3.4A')
            self.boss.write('PC+3.400')
            log.info(self.boss.read())

            time_elapsed = 0

            buffer_empty = False
            while buffer_empty == False:
                try:
                    log.info(self.boss.read())
                except:
                    log.info('Buffer empty')
                    buffer_empty = True
                else:
                    log.info('Purged 1 line from buffer')
                    log.info('Residue in buffer')

            while time_elapsed < timeout_seconds * 2:
                voltage = self.boss.query_ascii_values('MV')[0]
                log.info(self.boss.read())
                current = self.boss.query_ascii_values('MI')[0]
                log.info(self.boss.read())

                time_elapsed = perf_counter() - test_start_time
                time_interval = time_elapsed - last_time
                delta_charge = time_interval / 3600 * current
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
                    'Voltage': voltage,
                    'Current': current,
                    'Charge': charge,
                    'Ah_V': Ah_V,
                    'SoC': charge / self.nominal_capacity
                }

                self.emit('results', data)
                log.debug("Emitting results: %s" % data)
                self.emit('progress', 100 * time_elapsed / timeout_seconds)

                if voltage >= self.charge_voltage:
                    log.info('Pack charged')
                    break

                sleep(0.5)
                if self.should_stop():
                    log.warning("Caught the stop flag in the procedure")
                    break
        
        log.info('Setting current to zero')
        self.boss.write('PC0')
        log.info(self.boss.read())

class MainWindow(ManagedWindow):

    def __init__(self):
        super().__init__(
            procedure_class=RandomProcedure,
            inputs=['nominal_capacity', 'charge_rate', 'discharge_rate', 'charge_voltage', 'discharge_voltage'],
            displays=['nominal_capacity', 'charge_rate', 'discharge_rate', 'charge_voltage', 'discharge_voltage'],
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