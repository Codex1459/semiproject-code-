import time
import sys

# Try importing hardware libraries
try:
    import RPi.GPIO as GPIO
    from gpiozero import Servo, RGBLED
    from gpiozero.pins.pigpio import PiGPIOFactory
except ImportError:
    print("This script must be run on the Raspberry Pi with RPi.GPIO and gpiozero installed.")
    sys.exit(1)

# Hardware Pins (Matching server.py)
SERVO_PIN = 18
LED_R = 22
LED_G = 27
LED_B = 25
TRIG_PIN = 23
ECHO_PIN = 24
BUZZER_PIN = 17

def test_buzzer():
    print("\n--- Testing Buzzer ---")
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(BUZZER_PIN, GPIO.OUT)
    
    def play_tone(freq, duration):
        period = 1.0 / freq
        delay = period / 2.0
        cycles = int(duration * freq)
        for _ in range(cycles):
            GPIO.output(BUZZER_PIN, True)
            time.sleep(delay)
            GPIO.output(BUZZER_PIN, False)
            time.sleep(delay)
            
    print("Playing 1000Hz tone...")
    play_tone(1000, 0.5)
    time.sleep(0.5)
    print("Playing 2000Hz tone...")
    play_tone(2000, 0.5)
    print("Buzzer test complete.")

def test_rgb_led():
    print("\n--- Testing RGB LED ---")
    # Change active_high to False if using a Common Anode LED
    rgb = RGBLED(red=LED_R, green=LED_G, blue=LED_B, active_high=True)
    
    print("Red...")
    rgb.color = (1, 0, 0)
    time.sleep(1)
    
    print("Green...")
    rgb.color = (0, 1, 0)
    time.sleep(1)
    
    print("Blue...")
    rgb.color = (0, 0, 1)
    time.sleep(1)
    
    print("White...")
    rgb.color = (1, 1, 1)
    time.sleep(1)
    
    print("Off...")
    rgb.color = (0, 0, 0)
    time.sleep(0.5)
    rgb.close() # release pins
    print("RGB LED test complete.")

def test_ultrasonic():
    print("\n--- Testing Ultrasonic Sensor ---")
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(TRIG_PIN, GPIO.OUT)
    GPIO.setup(ECHO_PIN, GPIO.IN)
    GPIO.output(TRIG_PIN, False)
    time.sleep(0.5)
    
    print("Reading distance for 3 seconds...")
    for i in range(5):
        GPIO.output(TRIG_PIN, True)
        time.sleep(0.00001)
        GPIO.output(TRIG_PIN, False)

        start_time = time.time()
        stop_time = time.time()
        timeout = start_time + 0.1

        while GPIO.input(ECHO_PIN) == 0:
            start_time = time.time()
            if start_time > timeout: break

        while GPIO.input(ECHO_PIN) == 1:
            stop_time = time.time()
            if stop_time > timeout: break

        elapsed = stop_time - start_time
        distance = (elapsed * 34300) / 2
        print(f"Reading {i+1}: {distance:.2f} cm")
        time.sleep(0.5)
    print("Ultrasonic test complete.")

def test_servo():
    print("\n--- Testing Servo Motor ---")
    factory = None
    try:
        factory = PiGPIOFactory()
        print("Using pigpio precision PWM for servo.")
    except Exception:
        print("WARNING: pigpiod not running. Servo might jitter.")
        
    servo = Servo(SERVO_PIN, pin_factory=factory)
    
    # 0.0 is approx 90 degrees (middle), -1.0 is 0 degrees (min)
    print("Moving to 90 degrees (Closed / 0.0)...")
    servo.value = 0.0
    time.sleep(2)
    
    print("Moving smoothly to 0 degrees (Open / -1.0)...")
    current_val = 0.0
    while current_val >= -1.0:
        servo.value = current_val
        current_val -= 0.05
        time.sleep(0.05)
        
    servo.value = -1.0
    time.sleep(2)
    
    print("Moving smoothly back to 90 degrees (Closed / 0.0)...")
    current_val = -1.0
    while current_val <= 0.0:
        servo.value = current_val
        current_val += 0.05
        time.sleep(0.05)
        
    servo.value = 0.0
    time.sleep(1)
    
    print("Stopping at 90 degrees and detaching.")
    servo.value = None # Detach to prevent humming
    servo.close()
    print("Servo test complete.")

if __name__ == '__main__':
    try:
        print("=== STARTING HARDWARE CALIBRATION TEST ===")
        test_rgb_led()
        test_buzzer()
        test_ultrasonic()
        test_servo()
        print("\n=== ALL TESTS COMPLETED SUCCESSFULLY ===")
    except KeyboardInterrupt:
        print("\nTest cancelled by user.")
    finally:
        try:
            GPIO.cleanup([TRIG_PIN, ECHO_PIN, BUZZER_PIN])
        except:
            pass
