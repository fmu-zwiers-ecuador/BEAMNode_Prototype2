The anemometer that we currently have is an QS-FS01 adafruit anemometer.
This is the sketch script for the arduino hooked up to the anemometer.
You will need the Arduino IDE to use this script to put onto the arduino if any changes are made.

// qs_fs01_Nano_v3_serial.ino
// Reads QS-FS01 analog output and prints to Serial.

const int ADC_PIN = A0;         // ADC1 pin (input only)
const float VREF = 5.0;         // Nano v3 ADC reference (approx)
const int ADC_MAX = 1023;       // 10-bit ADC

float runningTotal = 0.0;
unsigned long readingCount = 0;

void setup() {
  Serial.begin(9600);
  //int value = analogRead(A0);     // 0..4095
  delay(200);
  Serial.println("QS-FS01 Nano reader (0.4-2.0V) starting...");
}

void loop() {
  int raw = analogRead(ADC_PIN);
  float v = (raw / (float)ADC_MAX) * VREF;

  // QS-FS01 0.4-2.0V variant (common): map 0.4..2.0V to 0..32.4 m/s
  float wind_mph = 0;
  if (v > 0.45) {
    wind_mph = ((v - 0.4) / 1.6) * 32.4 * 2.237;
  }

  runningTotal += wind_mph;
  readingCount++;
  float avg_mph = runningTotal / readingCount;

  Serial.print("raw=");
  Serial.print(raw);
  Serial.print(" V=");
  Serial.print(v, 3);
  Serial.print(" wind_mph=");
  Serial.print(wind_mph, 2);
  Serial.print(" avg_mph=");
  Serial.print(avg_mph, 2);
  Serial.print(" over ");
  Serial.print(readingCount);
  Serial.println(" readings");

  delay(5000);
}
