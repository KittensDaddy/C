import time
import subprocess
from gpio_manager import GPIOManager

# Initialize GPIO manager
gpio_manager = GPIOManager()

def callback(channel):
    if gpio_manager.read(channel) == 1:
        # Command to execute when GPIO pin is triggered
        subprocess.run(['python3', '/home/test/6.py'])

try:
    # Add event detection on GPIO pin 16
    gpio_manager.claim_input(16)
    gpio_manager.set_alert_func(16, callback)
except RuntimeError as e:
    print(f"Failed to add edge detection: {e}")
    gpio_manager.close()  # Clean up GPIO setup on error

# Keep the script running
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    gpio_manager.close()
