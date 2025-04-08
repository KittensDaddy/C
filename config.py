##
 #  @filename   :   DEV_Config.py
 #  @brief      :   LCD hardware interface implements (GPIO, SPI)
 #  @author     :   Yehui from Waveshare
 #
 #  Copyright (C) Waveshare     July 10 2017
 #
 # Permission is hereby granted, free of charge, to any person obtaining a copy
 # of this software and associated documnetation files (the "Software"), to deal
 # in the Software without restriction, including without limitation the rights
 # to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 # copies of the Software, and to permit persons to  whom the Software is
 # furished to do so, subject to the following conditions:
 #
 # The above copyright notice and this permission notice shall be included in
 # all copies or substantial portions of the Software.
 #
 # THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 # IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 # FITNESS OR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 # AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 # LIABILITY WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 # OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 # THE SOFTWARE.
 #
 
import spidev
import time
import logging
import numpy as np
import lgpio
from gpio_manager import GPIOManager

#GPIO define
KEY_UP_PIN     = 6 
KEY_DOWN_PIN   = 19
KEY_LEFT_PIN   = 5
KEY_RIGHT_PIN  = 26
KEY_PRESS_PIN  = 13

KEY1_PIN       = 21
KEY2_PIN       = 20
KEY3_PIN       = 16

# Initialize GPIO manager
gpio_manager = GPIOManager()

class RaspberryPi:
    def __init__(self, spi=spidev.SpiDev(0, 0), spi_freq=40000000, rst=27, dc=25, bl=24, bl_freq=1000, i2c=None, i2c_freq=100000):
        self.np = np
        self.INPUT = False
        self.OUTPUT = True

        self.SPEED = spi_freq
        self.BL_freq = bl_freq

        self.GPIO_RST_PIN = rst
        self.GPIO_DC_PIN = dc
        self.GPIO_BL_PIN = bl
        self.bl_DutyCycle(0)

        # Initialize GPIO manager
        self.gpio_manager = gpio_manager

        # Initialize GPIO outputs
        self.gpio_manager.claim_output(rst, 0)
        self.gpio_manager.claim_output(dc, 0)
        self.gpio_manager.claim_output(bl, 0)

        # Initialize SPI
        self.SPI = spi
        if self.SPI is not None:
            self.SPI.max_speed_hz = spi_freq
            self.SPI.mode = 0b00

    def digital_write(self, Pin, value):
        self.gpio_manager.write(Pin, value)

    def digital_read(self, Pin):
        return self.gpio_manager.read(Pin)

    def delay_ms(self, delaytime):
        time.sleep(delaytime / 1000.0)

    def spi_writebyte(self, data):
        if self.SPI is not None:
            self.SPI.writebytes(data)

    def bl_DutyCycle(self, duty):
        self.gpio_manager.write(self.GPIO_BL_PIN, int(duty / 100))  # Ensure level is an integer
        
    def bl_Frequency(self, freq):  # Hz
        self.BL_freq = freq
           
    def module_init(self):
        if self.SPI is not None:
            self.SPI.max_speed_hz = self.SPEED        
            self.SPI.mode = 0b00     
        return 0

    def module_exit(self):
        logging.debug("spi end")
        if self.SPI is not None:
            self.SPI.close()
        
        logging.debug("gpio cleanup...")
        self.gpio_manager.write(self.GPIO_RST_PIN, 1)
        self.gpio_manager.write(self.GPIO_DC_PIN, 0)
        self.gpio_manager.close()
        time.sleep(0.001)

### END OF FILE ###