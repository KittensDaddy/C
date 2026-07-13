import RPi.GPIO as GPIO


class GPIOManager:
    """Compatibility wrapper for code that expects a GPIOManager class."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def initialize(self, mode=GPIO.BCM):
        if not self._initialized:
            GPIO.setmode(mode)
            self._initialized = True

    def setmode(self, mode):
        GPIO.setmode(mode)
        self._initialized = True

    def setup_pin(self, pin, direction, pull_up_down=None):
        if pull_up_down is None:
            GPIO.setup(pin, direction)
        else:
            GPIO.setup(pin, direction, pull_up_down=pull_up_down)

    def setup(self, pin, direction, pull_up_down=None):
        self.setup_pin(pin, direction, pull_up_down=pull_up_down)

    def read(self, pin):
        return GPIO.input(pin)

    def input(self, pin):
        return GPIO.input(pin)

    def output(self, pin, value):
        GPIO.output(pin, value)

    def add_event_detect(self, pin, edge, callback=None, bouncetime=0):
        GPIO.add_event_detect(pin, edge, callback=callback, bouncetime=bouncetime)

    def cleanup(self):
        GPIO.cleanup()
        self._initialized = False

    @property
    def BCM(self):
        return GPIO.BCM

    @property
    def BOARD(self):
        return GPIO.BOARD

    @property
    def IN(self):
        return GPIO.IN

    @property
    def OUT(self):
        return GPIO.OUT

    @property
    def HIGH(self):
        return GPIO.HIGH

    @property
    def LOW(self):
        return GPIO.LOW

    @property
    def PUD_UP(self):
        return GPIO.PUD_UP

    @property
    def PUD_DOWN(self):
        return GPIO.PUD_DOWN

    @property
    def RISING(self):
        return GPIO.RISING


# Backward-compatible singleton instance for optional direct use.
gpio_manager = GPIOManager()
