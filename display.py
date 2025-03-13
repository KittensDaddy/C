from INA219 import INA219
from PIL import ImageSequence, ImageFont, ImageDraw, Image
import LCD_1in44
import config
import RPi.GPIO as GPIO
import time
import re
from setting import debounce, options, OPTIONS_PER_PAGE, color_themes, KEY_UP_PIN, KEY_DOWN_PIN, KEY_LEFT_PIN, KEY_RIGHT_PIN, KEY_PRESS_PIN, KEY1_PIN, KEY2_PIN, KEY3_PIN, DEBOUNCE_TIME, stealth_mode_active, ina219

# Set default theme
current_theme = color_themes[0]
# Initialize LCD
disp = LCD_1in44.LCD()
disp.LCD_Init(LCD_1in44.SCAN_DIR_DFT)
disp.LCD_Clear()
width, height = disp.width, disp.height
image = Image.new('RGB', (width, height), color=(0, 0, 0))
draw = ImageDraw.Draw(image)
font = ImageFont.load_default()

def is_stealth_mode_active():
    return stealth_mode_active

def draw_battery_bar():
    bus_voltage = ina219.getBusVoltage_V()             # voltage on V- (load side)
    shunt_voltage = ina219.getShuntVoltage_mV() / 1000 # voltage between V+ and V- across the shunt
    p = (bus_voltage - 3) / 1.2 * 100
    if p > 100:
        p = 100
    if p < 0:
        p = 0

    # Determine battery bar color
    if p >= 60:
        color = (0, 255, 0)  # Green
    elif p >= 30:
        color = (255, 255, 0)  # Yellow
    else:
        color = (255, 0, 0)  # Red

    # Draw battery bar
    bar_width = int((width - 20) * (p / 100))
    draw.rectangle((10, height - 10, 10 + bar_width, height - 5), fill=color)
    draw.rectangle((10, height - 10, width - 10, height - 5), outline=current_theme["text"])

def display_message(message):
    while stealth_mode_active:
        exit_stealth_mode()
        time.sleep(0.1)  # Short delay to avoid rapid looping
    draw.rectangle((0, 0, width, height), outline=0, fill=current_theme["background"])
    draw_shadowed_text(draw, message, (10, 10), font, current_theme["text"], current_theme["shadow"])
    draw_battery_bar()  # Ensure battery bar is drawn
    disp.LCD_ShowImage(image, 0, 0)
    time.sleep(0.3) 

def display_top(message):
    while stealth_mode_active:
        exit_stealth_mode()
        time.sleep(0.1)  # Short delay to avoid rapid looping
    draw.rectangle((0, 0, 128, 20), outline=0, fill=current_theme["background"])
    message = re.sub(r'\x1b\[[0-?9;]*[mK]', '', message)  # Remove ANSI color codes
    draw_shadowed_text(draw, message, (10, 0), font, current_theme["text"], current_theme["shadow"])
    draw_battery_bar()  # Ensure battery bar is drawn
    disp.LCD_ShowImage(image, 0, 0)
    time.sleep(0.3) 

def draw_shadowed_text(draw, text, position, font, text_color, shadow_color):
    x, y = position
    draw.text((x + 1, y + 1), text, font=font, fill=shadow_color)  # Draw shadow
    draw.text((x, y), text, font=font, fill=text_color)  # Draw text


# Function to display specific lines from cracked.json on the LCD
def display_file_on_lcd(filename):
    debounce()
    while stealth_mode_active:
        exit_stealth_mode()
        time.sleep(0.1)  # Short delay to avoid rapid looping
    try:
        with open(filename, 'r') as file:
            content = file.readlines()
    except Exception as e:
        display_message(f"Error reading file: {e}")
        return

    # Clear the screen before displaying new content
    draw.rectangle((0, 0, width, height), outline=0, fill=current_theme["background"])

    # Store lines that contain 'ESSID' or 'PSK'
    filtered_lines = []
    current_essid = None
    current_psk = None

    for line in content:
        if '"essid"' in line:
            if current_essid and current_psk is not None:
                filtered_lines.append(current_essid)
                filtered_lines.append(current_psk)
                filtered_lines.append("------")
            current_essid = line.split(":")[1].strip().strip('",')
            current_psk = "null"  # Default value if no PSK is found
        elif '"psk"' in line:
            current_psk = line.split(":")[1].strip().strip('",')

    # Add the last ESSID and PSK if present
    if current_essid and current_psk is not None:
        filtered_lines.append(current_essid)
        filtered_lines.append(current_psk)
        filtered_lines.append("------")

    # Check if any lines were found
    if not filtered_lines:
        display_message("No relevant data found.")
        return

    # Display the filtered content with scrolling
    start_idx = 0  # Track starting index for scrolling
    scroll_speed = 0.1  # Initial scroll speed
    scroll_hold_time = 2  # Time to hold before increasing scroll speed
    hold_start_time = None

    while True:
        while stealth_mode_active:
            exit_stealth_mode()
            time.sleep(0.1)  # Short delay to avoid rapid looping
        # Clear the screen and draw the lines to display
        draw.rectangle((0, 0, width, height), outline=0, fill=current_theme["background"])
        for i in range(start_idx, min(start_idx + (height // 10), len(filtered_lines))):
            draw_shadowed_text(draw, filtered_lines[i], (10, (i - start_idx) * 10), font, current_theme["text"], current_theme["shadow"])

        draw_battery_bar()  # Ensure battery bar is drawn
        disp.LCD_ShowImage(image, 0, 0)

        if GPIO.input(KEY_UP_PIN) == GPIO.LOW:
            if hold_start_time is None:
                hold_start_time = time.time()
            elif time.time() - hold_start_time > scroll_hold_time:
                scroll_speed = 0.03  # Increase scroll speed after holding
            if start_idx > 0:  # Scroll up
                start_idx -= 1
            time.sleep(scroll_speed)  # Debounce

        elif GPIO.input(KEY_DOWN_PIN) == GPIO.LOW:
            if hold_start_time is None:
                hold_start_time = time.time()
            elif time.time() - hold_start_time > scroll_hold_time:
                scroll_speed = 0.03  # Increase scroll speed after holding
            if start_idx < len(filtered_lines) - (height // 10):  # Scroll down
                start_idx += 1
            time.sleep(scroll_speed)  # Debounce

        else:
            hold_start_time = None
            scroll_speed = 0.1  # Reset scroll speed when button is released

        if GPIO.input(KEY_PRESS_PIN) == GPIO.LOW:  # Exit on any key press
            break

    disp.LCD_Clear()  # Clear the display before returning to the menu

def display_message_with_wrap(message, append=False, top=False):
    while stealth_mode_active:
        exit_stealth_mode()
        time.sleep(0.1)  # Short delay to avoid rapid looping

    # Maximum characters that fit on one line
    max_chars_per_line = width // 6  # Approximation (depends on font size)
    
    # Remove ASCII color codes
    message = re.sub(r'\x1b\[[0-?9;]*[mK]', '', message)  # ANSI escape code for colors

    # Split message into lines
    words = message.split()
    current_line = ""
    displayed_lines = []

    for word in words:
        # Check if adding the next word exceeds the line length
        if len(current_line) + len(word) + 1 <= max_chars_per_line:
            current_line += " " + word if current_line else word
        else:
            displayed_lines.append(current_line)
            current_line = word

    # Add the last line if it exists
    if current_line:
        displayed_lines.append(current_line)

    # If appending, retain the previously displayed lines
    if append:
        # Store existing lines from the previous message (if any)
        existing_lines = getattr(display_message_with_wrap, 'existing_lines', [])
        displayed_lines = existing_lines + displayed_lines

    # Limit the displayed lines to the height of the display
    max_visible_lines = (height - 20) // 12  # Assuming each line is 10 pixels high
    if len(displayed_lines) > max_visible_lines:
        displayed_lines = displayed_lines[-max_visible_lines:]  # Keep only the last few lines that fit

    # Clear the display before showing new content
    draw.rectangle((0, 0, width, height), outline=0, fill=current_theme["background"])
    draw_battery_bar()

    # Display the lines
    for i, line in enumerate(displayed_lines):
        draw_shadowed_text(draw, line.strip(), (10, (i * 10) + 20), font, current_theme["text"], current_theme["shadow"])

    draw_battery_bar()  # Ensure battery bar is drawn
    disp.LCD_ShowImage(image, 0, 0)

    # Store the displayed lines for the next call
    display_message_with_wrap.existing_lines = displayed_lines

# Function to draw the current scroll window of options
def draw_menu(active_idx):
    while stealth_mode_active:
        exit_stealth_mode()
        time.sleep(0.1)  # Short delay to avoid rapid looping
    draw.rectangle((0, 0, width, height), outline=0, fill=current_theme["background"])  # Clear screen

    # Determine the start and end indices for the visible window
    start_idx = max(0, active_idx - OPTIONS_PER_PAGE + 1)  # Show last few options when at the bottom
    end_idx = min(len(options), start_idx + OPTIONS_PER_PAGE)

    # Draw options within the window
    for i in range(start_idx, end_idx):
        option = options[i]

        # Determine state display text
        if "state" in option:
            if isinstance(option["state"], bool):
                state_text = "On" if option["state"] else "Off"
            else:
                state_text = option["state"]
        else:
            state_text = ""

        if i == active_idx:
            draw_shadowed_text(draw, f"> {option['name']}: {state_text}", (6, (i - start_idx) * 10), font, current_theme["highlight"], current_theme["shadow"])  # Highlight active option
        else:
            draw_shadowed_text(draw, f"  {option['name']}: {state_text}", (6, (i - start_idx) * 10), font, current_theme["text"], current_theme["shadow"])  # Normal display

    # Draw the battery bar
    draw_battery_bar()

    disp.LCD_ShowImage(image, 0, 0)

# Display feedback on the screen
def exit_stealth_mode():
    global stealth_mode_active
    if GPIO.input(KEY3_PIN) == GPIO.LOW:
        disp.LCD_Clear()
        stealth_mode_active = False

def stealth():
    global stealth_mode_active

    # Ensure GPIO setup
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(KEY_LEFT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(KEY_RIGHT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    # Simple game variables
    ball_pos = [width // 2, height // 2]
    ball_dir = [1, 1]
    ball_speed = 2
    paddle_width = 20
    paddle_height = 5
    paddle_pos = [width // 2 - paddle_width // 2, height - 20]  # Adjusted for battery bar
    paddle_speed = 5

    while stealth_mode_active:
        # Clear the screen
        draw.rectangle((0, 0, width, height), outline=0, fill=current_theme["background"])

        # Move the ball
        ball_pos[0] += ball_dir[0] * ball_speed
        ball_pos[1] += ball_dir[1] * ball_speed

        # Ball collision with walls
        if ball_pos[0] <= 0 or ball_pos[0] >= width:
            ball_dir[0] = -ball_dir[0]
        if ball_pos[1] <= 0:
            ball_dir[1] = -ball_dir[1]
        if ball_pos[1] >= height - 20:  # Adjusted for battery bar
            ball_dir[1] = -ball_dir[1]

        # Ball collision with paddle
        if (paddle_pos[0] <= ball_pos[0] <= paddle_pos[0] + paddle_width and
                paddle_pos[1] <= ball_pos[1] <= paddle_pos[1] + paddle_height):
            ball_dir[1] = -ball_dir[1]

        # Draw the ball
        draw.ellipse((ball_pos[0] - 2, ball_pos[1] - 2, ball_pos[0] + 2, ball_pos[1] + 2), fill=current_theme["text"])

        # Draw the paddle
        draw.rectangle((paddle_pos[0], paddle_pos[1], paddle_pos[0] + paddle_width, paddle_pos[1] + paddle_height), fill=current_theme["text"])

        # Move the paddle
        if GPIO.input(KEY_LEFT_PIN) == GPIO.LOW and paddle_pos[0] > 0:
            paddle_pos[0] -= paddle_speed
        if GPIO.input(KEY_RIGHT_PIN) == GPIO.LOW and paddle_pos[0] < width - paddle_width:
            paddle_pos[0] += paddle_speed

        # Draw the battery bar
        draw_battery_bar()

        # Display the game
        disp.LCD_ShowImage(image, 0, 0)
        time.sleep(0.05)  # Adjust the delay as needed

        # Exit stealth mode
        exit_stealth_mode()

def splash_screen():
    text = "SUN"
    text_color = (0, 255, 255)
    text_shadow_color = (255, 0, 255)
    text_pos = [width // 2, height // 2]
    text_dir = [1, 1]
    text_speed = 2
    font_large = ImageFont.load_default()  # Use default font to avoid resource issues

    start_time = time.time()
    while time.time() - start_time < 6:
        if GPIO.input(KEY_UP_PIN) == GPIO.LOW or GPIO.input(KEY_DOWN_PIN) == GPIO.LOW or GPIO.input(KEY_PRESS_PIN) == GPIO.LOW or GPIO.input(KEY1_PIN) == GPIO.LOW or GPIO.input(KEY2_PIN) == GPIO.LOW or GPIO.input(KEY3_PIN) == GPIO.LOW:
            break

        draw.rectangle((0, 0, width, height), outline=0, fill=current_theme["background"])

        # Move the text
        text_pos[0] += text_dir[0] * text_speed
        text_pos[1] += text_dir[1] * text_speed

        # Text collision with walls
        if text_pos[0] <= 0 or text_pos[0] >= width - 50:  # Adjusted for text width
            text_dir[0] = -text_dir[0]
        if text_pos[1] <= 0 or text_pos[1] >= height - 24:  # Adjusted for text height
            text_dir[1] = -text_dir[1]

        # Draw text shadow
        draw.text((text_pos[0] + 2, text_pos[1] + 2), text, font=font_large, fill=text_shadow_color)
        # Draw text
        draw.text((text_pos[0], text_pos[1]), text, font=font_large, fill=text_color)

        draw_battery_bar()  # Ensure battery bar is drawn
        disp.LCD_ShowImage(image, 0, 0)
        time.sleep(0.05)

# Non-blocking I/O helper to read subprocess output
def read_output_nonblocking(process, output_lines):
    while stealth_mode_active:
        exit_stealth_mode()
        time.sleep(0.1)  # Short delay to avoid rapid looping
    global current_essid, current_mac

    essid_results = {}

    for line in iter(process.stdout.readline, ''):
        output_stripped = line.strip()

        # Display raw output (debugging line, can be removed)
        #display_message_with_wrap(f"Raw Output: {output_stripped}", append=False)
        print({output_stripped})
        # Display ESSID and signal strength while scanning
        essid_scan_match = re.search(r'^\s*\d+\s+(.+?)\s+\d+\s+\S+\s+(\d+db)', output_stripped)
        if essid_scan_match:
            essid_scan = essid_scan_match.group(1).strip()
            signal_strength = essid_scan_match.group(2).strip()
            display_message_with_wrap(f"Scanning: {essid_scan} ({signal_strength})", append=True)
            time.sleep(0.1)

        # Check for "Starting attacks against" message and update ESSID/MAC
        if "Starting attacks against" in output_stripped:
            essid_match = re.search(r'Starting attacks against (.+?) \((.*?)\)$', output_stripped)
            if essid_match:
                #disp.LCD_Clear()
                current_mac = essid_match.group(1).strip()  # Extract mac address
                new_essid = essid_match.group(2).strip()  # Extract essid
                if new_essid != "unknown":
                    current_essid = new_essid
                display_top(f"-> {current_essid}")
                time.sleep(0.2)  # Keep the message on the screen longer

        # Handle output like targets and clients found
        matches = re.findall(r'\x1b\[[0-9;]*m.*?Found.*?\x1b\[[0-9;]*m(\d+).*?target\(s\).*?\x1b\[[0-9;]*m(\d+).*?client\(s\)', output_stripped)
        for match in matches:
            target_count, client_count = match
            output_lines.append(f"{target_count} targets, {client_count} clients")

            # Update display with ESSID and other information
            #display_message_with_wrap(f"-> {current_essid}", append=True, top=True)
            time.sleep(0.1)  # Keep this info on the screen for 2 seconds before continuing

        # Display important lines from wifite output
        if "WPA Handshake" in output_stripped:
            essid_results[current_essid] = "cracked"
            display_message_with_wrap(f"{current_essid} -> cracked", append=True)
            time.sleep(0.1)
        elif "Cracked" in output_stripped:
            if "PSK/Password: N/A" in output_stripped:
                essid_results[current_essid] = "cracked"
                display_message_with_wrap(f"{current_essid} > cracked", append=True)
            else:
                essid_results[current_essid] = "psk"
                display_message_with_wrap(f"{current_essid} > password", append=True)
            time.sleep(0.1)
        elif "Failed" in output_stripped:
            essid_results[current_essid] = "failed"
            display_message_with_wrap(f"{current_essid} > failed", append=True)
            time.sleep(0.1)
