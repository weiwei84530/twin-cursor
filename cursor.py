# Standard library imports
import os
import sys
import json
import threading
import time

# Third-party package imports
import win32api
import win32con
import win32gui
import pygame
import ctypes
from ctypes import (
    windll, byref, wintypes
)
import pystray
from PIL import Image
import winreg


from interception import Interception, MouseStroke
from interception.constants import (
    MouseButtonFlag,
    FilterMouseButtonFlag
)

# Debug mode setting
debug_mode = '--debug' in sys.argv

# Windows API constants
ES_CONTINUOUS = 0x80000000

# Virtual screen metric constants
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77

# Utility functions
def resource_path(relative_path):
    # Resolve bundled resources both in source runs and in a frozen EXE:
    # PyInstaller extracts data files to sys._MEIPASS instead of the script dir
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


# File paths
CURSOR_IMAGE_PATH = resource_path("cursor.png")
ICON_IMAGE_PATH = resource_path("icon.png")


def log(*args, **kwargs):
    # Debug logging function
    if debug_mode:
        print(*args, **kwargs)


# Multi-mouse monitor class
class MultiMouseMonitor:

    class MouseDevice:
        def __init__(self, name, device_num):
            self.name = name
            self.device_num = device_num
            self.is_mirrored = False

    def __init__(self):

        # Initialize mouse button states
        self.button_states = {'left': False, 'right': False, 'middle': False}

        # Get virtual screen size
        self.width = windll.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        self.height = windll.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        self.virtual_x = windll.user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        self.virtual_y = windll.user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        log(f"Virtual screen size: {self.width}x{self.height}")
        log(f"Virtual screen position: ({self.virtual_x}, {self.virtual_y})")


        # Initialize window
        # Create a full-screen borderless window
        self.screen = pygame.display.set_mode((self.width, self.height), pygame.NOFRAME | pygame.DOUBLEBUF)
        # Get and save the window handle
        self.hwnd = pygame.display.get_wm_info()["window"]
        # Move the window to the virtual screen origin
        win32gui.SetWindowPos(
            self.hwnd,
            win32con.HWND_TOPMOST,
            self.virtual_x,
            self.virtual_y,
            self.width,
            self.height,
            win32con.SWP_SHOWWINDOW
        )
        # Set window attributes
        win32gui.SetWindowLong(
            self.hwnd,
            win32con.GWL_EXSTYLE,
            win32gui.GetWindowLong(self.hwnd, win32con.GWL_EXSTYLE) | win32con.WS_EX_LAYERED |
            win32con.WS_EX_TRANSPARENT |
            win32con.WS_EX_TOOLWINDOW |
            win32con.WS_EX_NOACTIVATE |
            win32con.WS_EX_TOPMOST
        )
        # Set the transparency color key
        win32gui.SetLayeredWindowAttributes(self.hwnd, win32api.RGB(0,0,0), 0, win32con.LWA_COLORKEY)


        # Load the cursor image
        self.cursor_image = pygame.image.load(CURSOR_IMAGE_PATH)
        log(f"Loaded cursor image: {CURSOR_IMAGE_PATH}")

        # Initialize Interception
        self.interception = Interception()


        # Detect input devices
        self.mouse_devices = {}
        device_count = 0
        for i in range(20):  # Interception supports up to 20 devices
            if self.interception.is_mouse(i):
                self.interception.devices[i].set_filter(FilterMouseButtonFlag.FILTER_MOUSE_ALL | FilterMouseButtonFlag.FILTER_MOUSE_MOVE)
                device_name = f"Mouse {chr(65 + device_count)}"
                self.mouse_devices[i] = self.MouseDevice(device_name, i)
                device_count += 1

                if device_count >= 2:  # Only handle the first two mice
                    break

        for device in self.mouse_devices.values():
            log(f"Found device [{device.name}]: {device.device_num}")
        if len(self.mouse_devices) < 2:
            print("No second mouse device detected, exiting the application.")
            sys.exit(1)


        # Load settings and create the system tray icon
        self.load_settings_from_registry()
        self.create_tray_icon()


        # Initialize the active device
        point = wintypes.POINT()
        windll.user32.GetCursorPos(byref(point))
        self.current_device = list(self.mouse_devices.values())[0]

        # Initialize cursor positions
        self.current_position_x = point.x
        self.current_position_y = point.y
        self.last_position_x = self.current_position_x
        self.last_position_y = self.current_position_y
        self.update_cursor_position()

        log(f"Initialized active device {self.current_device.name}")


        # Allow the screensaver and system sleep
        windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)











    # Tray / registry related functions
    def save_settings_to_registry(self):
        key_path = r"SOFTWARE\TwinCursor"
        key_name = "MirrorSettings"
        try:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
        except WindowsError:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE)

        settings = {}
        for device in self.mouse_devices.values():
            settings[str(device.device_num)] = {
                'is_mirrored': device.is_mirrored
            }
            log(f"Saving setting: device {device.device_num} ({device.name}) mirrored={device.is_mirrored}")
        settings_str = json.dumps(settings, ensure_ascii=False)
        winreg.SetValueEx(key, key_name, 0, winreg.REG_SZ, settings_str)
        winreg.CloseKey(key)
        log(f"Mirror settings saved to registry: {settings_str}")

    def load_settings_from_registry(self):
        try:
            key_path = r"SOFTWARE\TwinCursor"
            key_name = "MirrorSettings"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
            settings_str, _ = winreg.QueryValueEx(key, key_name)
            winreg.CloseKey(key)
            log(f"Read settings from registry: {settings_str}")
            settings = json.loads(settings_str)
            log(f"Parsed settings: {settings}")

            for device in self.mouse_devices.values():
                if str(device.device_num) in settings:
                    device.is_mirrored = settings[str(device.device_num)]['is_mirrored']
                    log(f"Loaded {device.name} mirror setting: {'On' if device.is_mirrored else 'Off'}")
                else:
                    log(f"No setting found for device {device.device_num} ({device.name}), using default")

            log("Mirror settings loaded from registry")

        except FileNotFoundError:
            log("No registry settings found, using defaults")
        except Exception as e:
            log(f"Failed to load settings from registry: {e}")

    def create_tray_icon(self):
        def create_icon():
            icon = Image.open(ICON_IMAGE_PATH).convert('RGBA')
            return icon

        def on_exit(icon, item):
            self.cleanup()
            log("Exited from tray icon")
            os._exit(0)

        def toggle_mirror_mouse(device_num):
            def _toggle(icon, item):
                device = self.mouse_devices[device_num]
                device.is_mirrored = not device.is_mirrored
                log(f"{device.name} button mirror: {'On' if device.is_mirrored else 'Off'}")
                self.save_settings_to_registry()
            return _toggle

        # Create submenu items
        mouse_submenus = []
        for device_num, device in self.mouse_devices.items():
            mouse_submenus.append(
                pystray.MenuItem(
                    device.name,
                    pystray.Menu(
                        pystray.MenuItem(
                            "Mirror Buttons",
                            toggle_mirror_mouse(device_num),
                            checked=lambda item, dn=device_num: self.mouse_devices[dn].is_mirrored
                        )
                    )
                )
            )

        # Create the tray icon
        self.icon = pystray.Icon(
            "TwinCursor",
            create_icon(),
            "TwinCursor",
            menu=pystray.Menu(
                pystray.MenuItem("Options", pystray.Menu(*mouse_submenus)),
                pystray.MenuItem("Exit", on_exit)
            )
        )

        # Run the tray icon in a new thread
        threading.Thread(target=self.icon.run, daemon=True).start()






    # Handle a mouse event
    def handle_mouse_event(self, device_num, stroke):

        if device_num not in self.mouse_devices:
            return
        device = self.mouse_devices[device_num]


        # Block the event if this device is not the active one and any button is held down
        has_button_pressed = any(self.button_states.values())
        if self.current_device != device and has_button_pressed:
            return

        # If the device changed, transfer control to it
        if self.current_device != device:
            self.current_device = device
            log(f"Switched control to device {device.name}")

            # Move the real mouse cursor to the last recorded position
            point = wintypes.POINT()
            windll.user32.GetCursorPos(byref(point))
            self.current_position_x = point.x
            self.current_position_y = point.y
            windll.user32.SetCursorPos(int(self.last_position_x), int(self.last_position_y))
            log(f"Moved cursor to: {self.last_position_x}, {self.last_position_y}")

            # Swap the current and last positions
            self.current_position_x, self.last_position_x = self.last_position_x, self.current_position_x
            self.current_position_y, self.last_position_y = self.last_position_y, self.current_position_y
            self.update_cursor_position()

        # Handle move events
        if stroke.x != 0 or stroke.y != 0:
            # Update cursor position
            log(f"{device.name} moved: x={stroke.x:+4d}, y={stroke.y:+4d}")

        # Handle button events
        if stroke.button_flags != 0:
            def get_button_name(button_flags):
                """Get the button name."""
                if button_flags & MouseButtonFlag.MOUSE_LEFT_BUTTON_DOWN:
                    return "Left Down"
                elif button_flags & MouseButtonFlag.MOUSE_LEFT_BUTTON_UP:
                    return "Left Up"
                elif button_flags & MouseButtonFlag.MOUSE_RIGHT_BUTTON_DOWN:
                    return "Right Down"
                elif button_flags & MouseButtonFlag.MOUSE_RIGHT_BUTTON_UP:
                    return "Right Up"
                elif button_flags & MouseButtonFlag.MOUSE_MIDDLE_BUTTON_DOWN:
                    return "Middle Down"
                elif button_flags & MouseButtonFlag.MOUSE_MIDDLE_BUTTON_UP:
                    return "Middle Up"
                elif button_flags & MouseButtonFlag.MOUSE_WHEEL:
                    return "Wheel"
                elif button_flags & MouseButtonFlag.MOUSE_HWHEEL:
                    return "Horizontal Wheel"
                return "Unknown"

            button_name = get_button_name(stroke.button_flags)

            # Check whether the buttons need to be mirrored
            if device.is_mirrored:
                # Mirror the left and right buttons
                if stroke.button_flags & MouseButtonFlag.MOUSE_LEFT_BUTTON_DOWN:
                    stroke.button_flags = (stroke.button_flags & ~MouseButtonFlag.MOUSE_LEFT_BUTTON_DOWN) | MouseButtonFlag.MOUSE_RIGHT_BUTTON_DOWN
                    button_name = "Right Down"
                elif stroke.button_flags & MouseButtonFlag.MOUSE_LEFT_BUTTON_UP:
                    stroke.button_flags = (stroke.button_flags & ~MouseButtonFlag.MOUSE_LEFT_BUTTON_UP) | MouseButtonFlag.MOUSE_RIGHT_BUTTON_UP
                    button_name = "Right Up"
                elif stroke.button_flags & MouseButtonFlag.MOUSE_RIGHT_BUTTON_DOWN:
                    stroke.button_flags = (stroke.button_flags & ~MouseButtonFlag.MOUSE_RIGHT_BUTTON_DOWN) | MouseButtonFlag.MOUSE_LEFT_BUTTON_DOWN
                    button_name = "Left Down"
                elif stroke.button_flags & MouseButtonFlag.MOUSE_RIGHT_BUTTON_UP:
                    stroke.button_flags = (stroke.button_flags & ~MouseButtonFlag.MOUSE_RIGHT_BUTTON_UP) | MouseButtonFlag.MOUSE_LEFT_BUTTON_UP
                    button_name = "Left Up"

            is_mirrored_text = "(mirrored)" if device.is_mirrored else ""
            log(f"{device.name} {button_name} {is_mirrored_text}")

            # Update button states
            if "Left Down" in button_name:
                self.button_states['left'] = True
            elif "Left Up" in button_name:
                self.button_states['left'] = False
            elif "Right Down" in button_name:
                self.button_states['right'] = True
            elif "Right Up" in button_name:
                self.button_states['right'] = False
            elif "Middle Down" in button_name:
                self.button_states['middle'] = True
            elif "Middle Up" in button_name:
                self.button_states['middle'] = False

            # Show wheel data
            # Note: some mouse devices report no wheel data yet can still perform wheel actions
            if stroke.button_data != 0:
                log(f"    Wheel data: {stroke.button_data}")

        # Forward the event
        self.interception.send(device_num, stroke)



    # Update the cursor position on the virtual screen overlay
    def update_cursor_position(self):
        old_screen_x = self.current_position_x - self.virtual_x
        old_screen_y = self.current_position_y - self.virtual_y

        new_screen_x = self.last_position_x - self.virtual_x
        new_screen_y = self.last_position_y - self.virtual_y

        self.screen.fill((0,0,0), (old_screen_x, old_screen_y, self.cursor_image.get_width(), self.cursor_image.get_height()))

        self.screen.blit(self.cursor_image, (new_screen_x, new_screen_y))

        pygame.display.update([
            (old_screen_x, old_screen_y, self.cursor_image.get_width(), self.cursor_image.get_height()),
            (new_screen_x, new_screen_y, self.cursor_image.get_width(), self.cursor_image.get_height())
        ])







    # Main monitoring loop
    def monitor_loop(self):
        try:
            while True:
                # Wait for an input event with a 1-second timeout
                device_num = self.interception.await_input(1000)

                if device_num is not None:
                    device = self.interception.devices[device_num]
                    stroke = device.receive()

                    if stroke and isinstance(stroke, MouseStroke):
                        self.handle_mouse_event(device_num, stroke)

                time.sleep(0.001)

        except Exception as e:
            log(f"Monitor error: {e}")
            self.cleanup()


    # Clean up resources
    def cleanup(self):
        if hasattr(self, 'icon') and self.icon:
            self.icon.stop()
        if hasattr(self, 'interception') and self.interception:
            self.interception.destroy()





if __name__ == "__main__":
    monitor = MultiMouseMonitor() # Initialize the multi-mouse monitor

    log("\nMonitoring mouse activity in the background...")
    log("Press Ctrl+C to interrupt the program")

    try:
        monitor.monitor_loop()

    except KeyboardInterrupt:
        log("\nInterrupt signal received, shutting down...")
        monitor.cleanup()
        log("Program terminated")
