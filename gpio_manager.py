import lgpio

class GPIOManager:
    def __init__(self, chip_number=0):
        self.chip = lgpio.gpiochip_open(chip_number)

    def claim_output(self, pin, initial_value=0):
        lgpio.gpio_claim_output(self.chip, pin, initial_value)

    def claim_input(self, pin):
        lgpio.gpio_claim_input(self.chip, pin)

    def write(self, pin, value):
        lgpio.gpio_write(self.chip, pin, value)

    def read(self, pin):
        return lgpio.gpio_read(self.chip, pin)

    def close(self):
        lgpio.gpiochip_close(self.chip)
