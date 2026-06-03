/*
  vapona_sensor.ino  —  ESP32 firmware for VOC / Vapona incubator monitoring

  Hardware per incubator unit:
    - 2x PID sensor (analog, e.g. Spec Sensors DGS-VOC)
        Front sensor → GPIO34 (ADC1_CH6)
        Back  sensor → GPIO35 (ADC1_CH7)
    - 1x DS18B20 temperature sensor → GPIO4 (1-Wire)
    - Power: 3.3V / GND rails shared

  Posts JSON to the local Flask server every READ_INTERVAL_MS milliseconds.

  Endpoint:  POST http://<SERVER_IP>:<PORT>/reading
  Body:      { "incubator_id": N, "position": "front"|"back",
               "voc_ppm": 0.42, "temp_c": 27.3 }

  Two POST requests are sent per cycle — one for front, one for back sensor.

  Configuration — edit the section below before flashing.
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <OneWire.h>
#include <DallasTemperature.h>

// ── User configuration ────────────────────────────────────────────────────────

const char* WIFI_SSID     = "YourNetworkName";
const char* WIFI_PASSWORD = "YourNetworkPassword";

// IP of the PC running incubation_app.py  (check the status bar in the app)
const char* SERVER_IP   = "192.168.1.100";
const int   SERVER_PORT = 5151;

// Which incubator this unit belongs to (must match the ID in the app)
const int   INCUBATOR_ID = 1;

// Reading interval (milliseconds).  Default: 5 minutes.
const unsigned long READ_INTERVAL_MS = 5UL * 60UL * 1000UL;

// ── Pin definitions ───────────────────────────────────────────────────────────

#define PIN_SENSOR_FRONT  34    // ADC1_CH6
#define PIN_SENSOR_BACK   35    // ADC1_CH7
#define PIN_DS18B20        4    // 1-Wire data

// ── PID sensor calibration ────────────────────────────────────────────────────
// The PID sensor outputs 0–3.3 V mapped to 0–20 ppm (typical full-scale).
// Adjust SENSOR_FULL_SCALE_PPM to match your specific sensor data sheet.
// ESP32 ADC is 12-bit (0–4095) at 3.3 V reference.
//
// ppm = (adc_raw / 4095.0) * SENSOR_FULL_SCALE_PPM
//
// For DDVP monitoring, typical operational range is 0.0 – 1.0 ppm.
// A 20 ppm full-scale sensor gives ~0.005 ppm per ADC count — adequate resolution.

const float SENSOR_FULL_SCALE_PPM = 20.0f;

// Offset correction per sensor (in ppm).  Zero = no correction.
// Measure each sensor in clean air and adjust if non-zero.
const float FRONT_OFFSET_PPM = 0.0f;
const float BACK_OFFSET_PPM  = 0.0f;

// ── Globals ───────────────────────────────────────────────────────────────────

OneWire           oneWire(PIN_DS18B20);
DallasTemperature tempSensor(&oneWire);
unsigned long     lastReadTime = 0;
char              serverUrl[128];

// ── Setup ─────────────────────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== Vapona Sensor v1.0 ===");
  Serial.printf("Incubator ID: %d\n", INCUBATOR_ID);

  // ADC configuration
  analogSetAttenuation(ADC_11db);   // full 0–3.3 V range
  analogSetWidth(12);               // 12-bit (0–4095)

  // DS18B20
  tempSensor.begin();
  Serial.printf("DS18B20 devices found: %d\n", tempSensor.getDeviceCount());

  // WiFi
  Serial.printf("Connecting to WiFi: %s", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nConnected! IP: %s\n", WiFi.localIP().toString().c_str());

  // Build base URL
  snprintf(serverUrl, sizeof(serverUrl),
           "http://%s:%d/reading", SERVER_IP, SERVER_PORT);
  Serial.printf("POST target: %s\n", serverUrl);

  // Take first reading immediately
  lastReadTime = millis() - READ_INTERVAL_MS;
}

// ── Main loop ─────────────────────────────────────────────────────────────────

void loop() {
  unsigned long now = millis();

  // Reconnect WiFi if dropped
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi lost — reconnecting…");
    WiFi.reconnect();
    delay(3000);
    return;
  }

  if (now - lastReadTime >= READ_INTERVAL_MS) {
    lastReadTime = now;
    takeAndSendReadings();
  }
}

// ── Reading logic ─────────────────────────────────────────────────────────────

float adcToPpm(int raw, float offset) {
  float ppm = ((float)raw / 4095.0f) * SENSOR_FULL_SCALE_PPM + offset;
  return max(0.0f, ppm);   // clamp at 0
}

int oversampleADC(int pin, int samples = 16) {
  long sum = 0;
  for (int i = 0; i < samples; i++) {
    sum += analogRead(pin);
    delayMicroseconds(100);
  }
  return (int)(sum / samples);
}

float readTemperature() {
  tempSensor.requestTemperatures();
  float t = tempSensor.getTempCByIndex(0);
  if (t == DEVICE_DISCONNECTED_C || t < -50 || t > 85) {
    return NAN;
  }
  return t;
}

bool postReading(const char* position, float voc_ppm, float temp_c) {
  if (WiFi.status() != WL_CONNECTED) return false;

  HTTPClient http;
  http.begin(serverUrl);
  http.addHeader("Content-Type", "application/json");

  StaticJsonDocument<256> doc;
  doc["incubator_id"] = INCUBATOR_ID;
  doc["position"]     = position;
  doc["voc_ppm"]      = (double)roundf(voc_ppm * 10000.0f) / 10000.0;
  if (!isnan(temp_c)) {
    doc["temp_c"] = (double)roundf(temp_c * 10.0f) / 10.0;
  }

  char body[200];
  serializeJson(doc, body, sizeof(body));

  int code = http.POST(body);
  http.end();

  if (code == 200) {
    Serial.printf("  POST OK  %-5s  %.4f ppm", position, voc_ppm);
    if (!isnan(temp_c)) Serial.printf("  %.1f°C", temp_c);
    Serial.println();
    return true;
  } else {
    Serial.printf("  POST FAIL %-5s  HTTP %d\n", position, code);
    return false;
  }
}

void takeAndSendReadings() {
  Serial.printf("[%lus] Reading sensors…\n", millis() / 1000);

  // Oversample ADC for noise reduction
  int rawFront = oversampleADC(PIN_SENSOR_FRONT);
  int rawBack  = oversampleADC(PIN_SENSOR_BACK);
  float temp_c = readTemperature();

  float ppmFront = adcToPpm(rawFront, FRONT_OFFSET_PPM);
  float ppmBack  = adcToPpm(rawBack,  BACK_OFFSET_PPM);

  Serial.printf("  Front: ADC %d → %.4f ppm\n", rawFront, ppmFront);
  Serial.printf("  Back:  ADC %d → %.4f ppm\n", rawBack,  ppmBack);
  if (!isnan(temp_c)) Serial.printf("  Temp:  %.1f°C\n", temp_c);

  // Post both sensors — temp only sent with front reading to avoid duplicates
  postReading("front", ppmFront, temp_c);
  delay(200);
  postReading("back",  ppmBack,  NAN);
}
