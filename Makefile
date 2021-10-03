PROJECT := nixie-control-board
FQBN := arduino:avr:uno
BOARD := /dev/ttyACM0

all:
	arduino-cli compile --fqbn $(FQBN) $(PROJECT)

upload:
	arduino-cli compile --fqbn $(FQBN) $(PROJECT)
	sudo arduino-cli upload -p $(BOARD) --fqbn $(FQBN) $(PROJECT)

connect:
	minicom -D $(BOARD) -b 9600

clean:
	arduino-cli compile --clean --fqbn $(FQBN) $(PROJECT)
