OPTIONS_PER_PAGE = 11
DEBOUNCE_TIME = 0.15
current_index = 0
KEY_UP_PIN = 6
KEY_DOWN_PIN = 19
KEY_LEFT_PIN = 5
KEY_RIGHT_PIN = 26
KEY_PRESS_PIN = 13
KEY1_PIN = 21
KEY2_PIN = 20
KEY3_PIN = 16
stealth_mode_active = False
from INA219 import INA219
ina219 = INA219(addr=0x43)

from PIL import ImageFont, Image, ImageDraw

def debounce():
    time.sleep(DEBOUNCE_TIME)

def apply_theme(theme):
    global current_theme, image, draw, font
    current_theme = theme
    image = Image.new('RGB', (width, height), color=current_theme["background"])
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

color_themes = [
    {"name": "Default", "background": (30, 30, 30), "text": (255, 255, 255), "highlight": (255, 255, 0), "shadow": (50, 50, 50)},
    {"name": "Cool Blue", "background": (50, 50, 50), "text": (173, 216, 230), "highlight": (0, 255, 255), "shadow": (30, 30, 30)},
    {"name": "Warm Red", "background": (70, 70, 70), "text": (255, 182, 193), "highlight": (255, 69, 0), "shadow": (50, 50, 50)},
    {"name": "Green Forest", "background": (40, 40, 40), "text": (144, 238, 144), "highlight": (34, 139, 34), "shadow": (20, 20, 20)},
    {"name": "Cyberpunk", "background": (20, 20, 20), "text": (0, 255, 0), "highlight": (255, 0, 255), "shadow": (10, 10, 10)},
    {"name": "Monochrome", "background": (10, 10, 10), "text": (200, 200, 200), "highlight": (255, 255, 255), "shadow": (5, 5, 5)}
]
