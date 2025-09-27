import os
from flask import Flask, request, jsonify
import google.generativeai as genai
import requests
from dotenv import load_dotenv
import json

# Load environment variables from a .env file
load_dotenv()

# --- Configuration ---

# Configure the Generative AI model using the API key from environment variables
try:
    gemini_api_key = os.environ.get("GOOGLE_API_KEY")
    if not gemini_api_key:
        raise ValueError("ERROR: GOOGLE_API_KEY environment variable not set.")
    genai.configure(api_key=gemini_api_key)
    # Updated model name to a current version
    llm = genai.GenerativeModel('gemini-2.5-flash')
except (ValueError, Exception) as e:
    print(e)
    llm = None

# Get the Google Maps API Key from environment variables
MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Helper Functions ---

def get_lat_lng(location_name):
    """Geocodes a location name to latitude and longitude using Google Geocoding API."""
    if not MAPS_API_KEY:
        print("ERROR: GOOGLE_MAPS_API_KEY environment variable not set.")
        return None
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={location_name}&key={MAPS_API_KEY}"
    try:
        response = requests.get(url)
        response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)
        data = response.json()
        if data["status"] == "OK":
            location = data["results"][0]["geometry"]["location"]
            return {"lat": location["lat"], "lng": location["lng"]}
        else:
            print(f"Geocoding failed for {location_name}: {data['status']}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Error geocoding location: {e}")
        return None

def get_air_quality(lat, lng):
    """Fetches air quality data from the Google Air Quality API."""
    if not MAPS_API_KEY:
        print("ERROR: GOOGLE_MAPS_API_KEY environment variable not set.")
        return None
    url = "https://airquality.googleapis.com/v1/currentConditions:lookup"
    params = {"key": MAPS_API_KEY}
    payload = {"location": {"latitude": lat, "longitude": lng}}
    try:
        response = requests.post(url, params=params, json=payload)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching air quality data: {e}")
        return None

# --- API Endpoints ---

@app.route('/api/geocode', methods=['POST'])
def geocode_location():
    """Geocodes a location name to latitude and longitude."""
    data = request.get_json()
    location_name = data.get('location')

    if not location_name:
        return jsonify({"error": "Location name is required"}), 400

    coordinates = get_lat_lng(location_name)

    if not coordinates:
        return jsonify({"error": f"Could not find coordinates for '{location_name}'"}), 404

    return jsonify(coordinates)

@app.route('/api/query', methods=['POST'])
def handle_query():
    """Main endpoint to handle user's natural language queries."""
    if not llm:
        return jsonify({"error": "LLM not configured. Check your GOOGLE_API_KEY."}), 500
    if not MAPS_API_KEY:
        return jsonify({"error": "Google Maps API key not configured."}), 500

    data = request.get_json()
    user_prompt = data.get('prompt')

    if not user_prompt:
        return jsonify({"error": "Prompt is required"}), 400

    try:
        # 1. Use LLM to extract the location
        location_extraction_prompt = f"Extract only the city and country from the following text, in the format 'City, Country'. If a specific city is not mentioned, identify the most likely major city based on the context. Text: '{user_prompt}'"
        location_response = llm.generate_content(location_extraction_prompt)
        location_name = location_response.text.strip()

        if not location_name or "could not" in location_name.lower():
            return jsonify({"error": "Could not identify a location from your query."}), 400

        # 2. Geocode the location
        coordinates = get_lat_lng(location_name)
        if not coordinates:
            return jsonify({"error": f"Could not find coordinates for '{location_name}'"}), 404

        # 3. Fetch new air quality data
        aqi_data = get_air_quality(coordinates['lat'], coordinates['lng'])
        if not aqi_data:
            return jsonify({"error": "Could not retrieve air quality data for the location."}), 500

        # 4. Use LLM to generate a human-readable explanation
        explanation_prompt = f"""
        Based on the provided Air Quality Index (AQI) data, generate a comprehensive, easy-to-understand summary for a non-expert.

        Your response should be a JSON object with the following structure:
        {{
          "overview": {{
            "aqi": <numeric_aqi_value>,
            "category": "<category_string>",
            "dominant_pollutant": "<dominant_pollutant_name>",
            "dominant_pollutant_description": "<brief_description_of_pollutant>",
            "health_summary": "<concise_health_summary>"
          }},
          "recommendations": {{
            "general_population": "<recommendation_for_general_public>",
            "sensitive_groups": "<recommendation_for_sensitive_groups>"
          }},
          "pollutants": [
            {{
              "name": "<pollutant_name>",
              "aqi": <pollutant_aqi_value>,
              "concentration": "<concentration_value> <units>"
            }}
          ]
        }}

        **Data:**
        ```json
        {json.dumps(aqi_data, indent=2)}
        ```
        """
        explanation_response = llm.generate_content(explanation_prompt)
        # Clean the response to ensure it's valid JSON
        cleaned_text = explanation_response.text.strip().replace('```json', '').replace('```', '')
        explanation_json = json.loads(cleaned_text)


        return jsonify({
            "coordinates": coordinates,
            "explanation": explanation_json,
            "raw_aqi_data": aqi_data
        })

    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return jsonify({"error": "An internal server error occurred."}), 500

@app.route('/api/forecast', methods=['POST'])
def get_forecast_data():
    """Provides sample forecast data."""
    # This is mock data. In a real application, you would fetch this from a weather/air quality API.
    forecast_data = {
        "labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "datasets": [
            {
                "label": "Predicted AQI",
                "data": [58, 62, 55, 65, 70, 68, 72],
                "fill": False,
                "borderColor": 'rgb(75, 192, 192)',
                "tension": 0.1
            }
        ]
    }
    return jsonify(forecast_data)


@app.route('/api/heatmap-data', methods=['POST'])
def get_heatmap_data():
    """Provides sample data for the heatmap."""
    # This is mock data. A real implementation would fetch live data 
    # for multiple points within the map's current view.
    sample_points = [
        {"lat": 33.75, "lng": -84.38, "aqi": 55}, # Downtown Atlanta
        {"lat": 33.84, "lng": -84.36, "aqi": 65}, # Buckhead
        {"lat": 33.77, "lng": -84.39, "aqi": 45}, # Georgia Tech
        {"lat": 33.75, "lng": -84.34, "aqi": 70}, # Inman Park
        {"lat": 33.88, "lng": -84.46, "aqi": 58}, # Smyrna
        {"lat": 33.63, "lng": -84.42, "aqi": 62}, # Near Airport
    ]
    return jsonify(sample_points)

if __name__ == '__main__':
    # Runs the app in debug mode. In a production environment, use a WSGI server like Gunicorn.
    app.run(debug=True, port=5000)