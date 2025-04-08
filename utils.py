"""
GPIO Utility module that provides a common interface for both RPi.GPIO and LGPIO.
This helps in migrating from RPi.GPIO to LGPIO while maintaining compatibility.
"""

import sys
import time

# Flag to indicate which GPIO library to use
USE_LGPIO = True

try:
    if USE_LGPIO:
        import lgpio
        print("Using LGPIO library")
    else:
        import RPi.GPIO as GPIO
        print("Using RPi.GPIO library")
except ImportError as e:
    print(f"Error importing GPIO library: {e}")
    print("Falling back to dummy GPIO implementation for development/testing")
    USE_LGPIO = False
    # Create a dummy GPIO class for development/testing
    class DummyGPIO:
        BCM = "BCM"
        BOARD = "BOARD"
        IN = "IN"
        OUT = "OUT"
        PUD_UP = "PUD_UP"
        PUD_DOWN = "PUD_DOWN"
        LOW = 0
        HIGH = 1
        RISING = "RISING"
        FALLING = "FALLING"
        BOTH = "BOTH"
        
        @staticmethod
        def setmode(mode):
            print(f"GPIO.setmode({mode})")
            
        @staticmethod
        def setup(pin, direction, pull_up_down=None):
            pull = pull_up_down if pull_up_down else "None"
            print(f"GPIO.setup({pin}, {direction}, {pull})")
            
        @staticmethod
        def input(pin):
            print(f"GPIO.input({pin})")
            return False
            
        @staticmethod
        def output(pin, value):
            print(f"GPIO.output({pin}, {value})")
            
        @staticmethod
        def cleanup():
            print("GPIO.cleanup()")
            
        @staticmethod
        def add_event_detect(pin, edge, callback=None, bouncetime=None):
            print(f"GPIO.add_event_detect({pin}, {edge}, {callback}, {bouncetime})")
    
    GPIO = DummyGPIO()

# Global GPIO chip handle for LGPIO
gpio_chip_handle = None

# Constants
BCM = "BCM"
BOARD = "BOARD"
IN = 0
OUT = 1
PUD_UP = 1
PUD_DOWN = 0
LOW = 0
HIGH = 1
RISING = 1
FALLING = 0
BOTH = 2

def initialize():
    """Initialize the GPIO system"""
    global gpio_chip_handle
    
    if USE_LGPIO:
        try:
            gpio_chip_handle = lgpio.gpiochip_open(0)
            return True
        except Exception as e:
            print(f"Failed to initialize LGPIO: {e}")
            return False
    else:
        try:
            GPIO.setmode(GPIO.BCM)
            return True
        except Exception as e:
            print(f"Failed to initialize RPi.GPIO: {e}")
            return False

def cleanup():
    """Clean up GPIO resources"""
    if USE_LGPIO:
        if gpio_chip_handle is not None:
            try:
                lgpio.gpiochip_close(gpio_chip_handle)
            except Exception as e:
                print(f"Error during LGPIO cleanup: {e}")
    else:
        try:
            GPIO.cleanup()
        except Exception as e:
            print(f"Error during RPi.GPIO cleanup: {e}")

def setup(pin, direction, pull_up_down=None):
    """Setup a GPIO pin"""
    if USE_LGPIO:
        try:
            if direction == IN:
                pull = lgpio.SET_PULL_UP if pull_up_down == PUD_UP else lgpio.SET_PULL_DOWN
                lgpio.gpio_claim_input(gpio_chip_handle, pin, pull)
            else:  # OUT
                lgpio.gpio_claim_output(gpio_chip_handle, pin)
        except Exception as e:
            print(f"Error setting up pin {pin}: {e}")
    else:
        GPIO.setup(pin, GPIO.IN if direction == IN else GPIO.OUT, 
                  pull_up_down=GPIO.PUD_UP if pull_up_down == PUD_UP else GPIO.PUD_DOWN if pull_up_down == PUD_DOWN else None)

def input(pin):
    """Read value from a GPIO pin"""
    if USE_LGPIO:
        try:
            return lgpio.gpio_read(gpio_chip_handle, pin)
        except Exception as e:
            print(f"Error reading from pin {pin}: {e}")
            return 0
    else:
        return GPIO.input(pin)

def output(pin, value):
    """Write value to a GPIO pin"""
    if USE_LGPIO:
        try:
            lgpio.gpio_write(gpio_chip_handle, pin, value)
        except Exception as e:
            print(f"Error writing to pin {pin}: {e}")
    else:
        GPIO.output(pin, value)

def add_event_detect(pin, edge, callback=None, bouncetime=None):
    """Add event detection to a GPIO pin"""
    if USE_LGPIO:
        try:
            if callback:
                lgpio.gpio_set_alert_func(gpio_chip_handle, pin, callback)
        except Exception as e:
            print(f"Error adding event detection to pin {pin}: {e}")
    else:
        try:
            edge_type = GPIO.RISING if edge == RISING else GPIO.FALLING if edge == FALLING else GPIO.BOTH
            GPIO.add_event_detect(pin, edge_type, callback=callback, bouncetime=bouncetime)
        except Exception as e:
            print(f"Error adding event detection to pin {pin}: {e}")
