# DMZ-EG4S20：50 MHz 晶振接在 R7
create_clock -name clk -period 20.000 -waveform {0.000 10.000} [get_ports clk]
