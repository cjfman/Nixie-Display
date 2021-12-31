PROJECT := nixie-control-board
FQBN := arduino:avr:uno
#BOARD := /dev/ttyACM0
BOARD := $(shell arduino-cli board list | grep arduino:avr:uno | head -n 1 | sed -e 's/ .*//')

find:
	echo Board '$(BOARD)'

all:
	arduino-cli compile --fqbn $(FQBN) $(PROJECT)

upload:
	arduino-cli compile --fqbn $(FQBN) $(PROJECT)
	arduino-cli upload -p $(BOARD) --fqbn $(FQBN) $(PROJECT)

connect:
	minicom -D $(BOARD) -b 9600

clean:
	arduino-cli compile --clean --fqbn $(FQBN) $(PROJECT)
