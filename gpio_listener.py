import RPi.GPIO as GPIO
import time
import subprocess

# Set up GPIO
GPIO.setmode(GPIO.BOARD)
GPIO.setup(16, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)  # GPIO pin 16

def callback(channel):
    if GPIO.input(channel) == GPIO.HIGH:
        # Command to execute when GPIO pin is triggered
        subprocess.run(['python3', '/home/test/6.py'])

try:
    # Add event detection on GPIO pin 16
    GPIO.add_event_detect(16, GPIO.RISING, callback=callback, bouncetime=300)
except RuntimeError as e:
    print(f"Failed to add edge detection: {e}")
    GPIO.cleanup()  # Clean up GPIO setup on error

# Keep the script running
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    GPIO.cleanup()
