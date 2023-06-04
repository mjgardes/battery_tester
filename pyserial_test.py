# %%
from time import sleep
import pyvisa
rm = pyvisa.ResourceManager('@py')
boss = rm.open_resource('GPIB0::8::INSTR')
# %%
boss = rm.open_resource('ASRL/dev/ttyUSB_Gearmo::INSTR')
# %%
boss.read_termination = '\r\n' # Stupid, but it works
boss.write_termination = '\r\n'
# %%
boss.baud_rate = 9600
# %%
boss.write('SR')
boss.query('?C')
# %%
print(boss.query('?M'))
boss.read_raw() # dummy read to flush the 'ok'
# %%
print(boss.query_ascii_values('MV'))
boss.read_raw() # dummy read to flush the 'ok'
# %%
print(boss.query_ascii_values('MI'))
boss.read_raw() # dummy read to flush the 'ok'
# %%
