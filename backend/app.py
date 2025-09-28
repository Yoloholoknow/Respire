import os
from flask import Flask, request, jsonify
import google.generativeai as genai
import requests
from dotenv import load_dotenv
import json
import traceback

# Load environment variables from a .env file
load_dotenv()

# --- Configuration ---

# Configure the Generative AI model
try:
    gemini_api_key = os.environ.get("GOOGLE_API_KEY")
    if not gemini_api_key:
        raise ValueError("ERROR: GOOGLE_API_KEY environment variable not set.")
    genai.configure(api_key=gemini_api_key)
    llm = genai.GenerativeModel('gemini-2.5-flash')
except (ValueError, Exception) as e:
    print(e)
    llm = None

MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")
WAQI_API_TOKEN = os.environ.get("WAQI_API_TOKEN")

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
        response.raise_for_status()
        data = response.json()
        if data["status"] == "OK":
            location = data["results"][0]["geometry"]["location"]
            # Round coordinates to reduce precision - Air Quality API works better with less precise coordinates
            lat = round(location["lat"], 4)
            lng = round(location["lng"], 4)
            print(f"Geocoded '{location_name}' to coordinates: {lat}, {lng}")
            return {"lat": lat, "lng": lng}
        else:
            print(f"Geocoding failed for {location_name}: {data['status']}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Error geocoding location: {e}")
        return None

def get_air_quality(lat, lng):
    """Fetches air quality data using the WAQI API for given coordinates.

    Returns a unified structure with keys: provider, raw, aqi, dominant_pollutant, pollutants (list).
    Falls back to an estimation if WAQI token is not configured or WAQI returns no data.
    """
    # Try WAQI first
    if WAQI_API_TOKEN:
        try:
            url = f"https://api.waqi.info/feed/geo:{lat};{lng}/?token={WAQI_API_TOKEN}"
            print(f"Querying WAQI for {lat}, {lng}")
            resp = requests.get(url, timeout=8)
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "ok" and isinstance(data.get("data"), dict):
                d = data["data"]
                overall_aqi = d.get("aqi")
                dominant = d.get("dominentpol")
                pollutants = []
                iaqi = d.get("iaqi", {})
                for code, val in iaqi.items():
                    # WAQI returns {pm25: {v: 12}, o3: {v: 5}, ...}
                    display = code.upper() if code else ""
                    pollutants.append({
                        "code": code,
                        "displayName": display,
                        "concentration": {"value": val.get("v") if isinstance(val, dict) else val, "units": "µg/m³"},
                        "aqi": None
                    })

                unified = {
                    "provider": "waqi",
                    "raw": data,
                    "aqi": overall_aqi,
                    "dominant_pollutant": dominant,
                    "pollutants": pollutants,
                    "city": d.get("city", {}).get("name") if d.get("city") else None,
                    "time": d.get("time")
                }
                return unified
            else:
                print(f"WAQI returned no data for {lat},{lng}: {data.get('status')}")
        except requests.exceptions.RequestException as e:
            print(f"WAQI request error: {e}")

    # If WAQI not configured or failed, fall back to estimation with synthetic pollutant data
    print("Falling back to estimated/local model for air quality (WAQI unavailable)")
    estimated_aqi = estimate_pollution_by_location(lat, lng)
    
    # Generate realistic pollutant breakdown based on estimated AQI
    estimated_pollutants = generate_estimated_pollutants(estimated_aqi, lat, lng)
    dominant_pollutant = get_dominant_pollutant(estimated_pollutants)
    
    unified = {
        "provider": "estimate",
        "raw": None,
        "aqi": estimated_aqi,
        "dominant_pollutant": dominant_pollutant,
        "pollutants": estimated_pollutants,
        "city": None,
        "time": None
    }
    return unified

def generate_estimated_pollutants(base_aqi, lat, lng):
    """Generate realistic pollutant breakdown based on estimated AQI and location."""
    # Common pollutants with their typical ranges and characteristics
    pollutant_info = {
        'pm25': {'name': 'PM2.5', 'units': 'µg/m³', 'base_ratio': 0.4},
        'pm10': {'name': 'PM10', 'units': 'µg/m³', 'base_ratio': 0.6},
        'o3': {'name': 'Ozone', 'units': 'µg/m³', 'base_ratio': 0.3},
        'no2': {'name': 'NO₂', 'units': 'µg/m³', 'base_ratio': 0.25},
        'so2': {'name': 'SO₂', 'units': 'µg/m³', 'base_ratio': 0.15},
        'co': {'name': 'CO', 'units': 'mg/m³', 'base_ratio': 0.1}
    }
    
    pollutants = []
    for code, info in pollutant_info.items():
        # Generate concentration based on AQI with some variation
        variation = (hash(f"{lat}_{lng}_{code}") % 40) - 20  # ±20% variation
        concentration = max(1, int(base_aqi * info['base_ratio'] * (1 + variation / 100)))
        
        # Convert concentration to approximate AQI for individual pollutant
        pollutant_aqi = min(500, max(0, int(concentration * 0.8 + (hash(f"{code}_{lat}") % 20) - 10)))
        
        pollutants.append({
            'code': code,
            'displayName': info['name'],
            'concentration': {'value': concentration, 'units': info['units']},
            'aqi': pollutant_aqi
        })
    
    return pollutants

def get_dominant_pollutant(pollutants):
    """Determine the dominant pollutant from the list."""
    if not pollutants:
        return None
    
    # Find pollutant with highest AQI
    dominant = max(pollutants, key=lambda p: p.get('aqi', 0))
    return dominant.get('code')

def format_air_quality_data(aqi_data):
    """
    Formats the raw AQI data into a structured JSON object with enhanced pollutant details.
    """
    aqi_categories = {
        (0, 50): ("Good", "Air quality is considered satisfactory, and air pollution poses little or no risk.", "Enjoy your usual outdoor activities.", "No specific recommendations needed."),
        (51, 100): ("Moderate", "Air quality is acceptable; however, for some pollutants there may be a moderate health concern for a very small number of people who are unusually sensitive to air pollution.", "No need to modify your usual activities unless you are unusually sensitive to a particular pollutant.", "People with respiratory or heart disease, the elderly, and children should consider reducing prolonged or heavy exertion."),
        (101, 150): ("Unhealthy for Sensitive Groups", "Members of sensitive groups may experience health effects. The general public is not likely to be affected.", "Consider making outdoor activities shorter and less intense. Go indoors if you have symptoms.", "People with respiratory or heart disease, the elderly, and children should reduce prolonged or heavy exertion."),
        (151, 200): ("Unhealthy", "Everyone may begin to experience health effects; members of sensitive groups may experience more serious health effects.", "Reduce or reschedule strenuous activities outdoors. Consider moving activities indoors.", "Sensitive groups should avoid all outdoor exertion."),
        (201, 300): ("Very Unhealthy", "Health alert: everyone may experience more serious health effects.", "Avoid all physical activity outdoors.", "Everyone should remain indoors and keep activity levels low."),
        (301, 500): ("Hazardous", "Health warnings of emergency conditions. The entire population is more likely to be affected.", "Remain indoors and keep windows and doors closed. Avoid all physical activity.", "Everyone should remain indoors and keep activity levels low.")
    }

    # Support unified WAQI-style structure created by get_air_quality
    if not aqi_data:
        return {"overview": {}, "recommendations": {}, "pollutants": []}

    if aqi_data.get('provider') == 'waqi':
        overall_aqi = aqi_data.get('aqi')
        dominant_pollutant_code = aqi_data.get('dominant_pollutant')
        pollutants_list = aqi_data.get('pollutants', [])
        # Convert pollutants_list into expected pollutants_data shape
        pollutants_data = []
        for p in pollutants_list:
            pollutants_data.append({
                'code': p.get('code'),
                'displayName': p.get('displayName'),
                'concentration': p.get('concentration'),
                'additionalInfo': {'aqi': p.get('aqi')}
            })
    else:
        # legacy/estimate - use direct pollutants data
        overall_aqi = aqi_data.get('aqi') if isinstance(aqi_data.get('aqi'), (int, float)) else 0
        dominant_pollutant_code = aqi_data.get('dominant_pollutant')
        pollutants_data = aqi_data.get('pollutants', [])
        
        # Ensure consistent structure for estimated data
        for p in pollutants_data:
            if 'additionalInfo' not in p:
                p['additionalInfo'] = {'aqi': p.get('aqi')}

    category, health_summary, general_rec, sensitive_rec = "Unknown", "No data available.", "No specific recommendations.", "No specific recommendations."
    for (lower_bound, upper_bound), (cat, summary, gen, sens) in aqi_categories.items():
        if lower_bound <= overall_aqi <= upper_bound:
            category = cat
            health_summary = summary
            general_rec = gen
            sensitive_rec = sens
            break

    # Enhanced pollutant information with health context
    pollutant_health_info = {
        'pm25': {
            'description': 'Fine particles that can penetrate deep into lungs and bloodstream',
            'sources': 'Vehicle exhaust, industrial emissions, wildfires',
            'health_effects': 'Respiratory and cardiovascular problems'
        },
        'pm10': {
            'description': 'Inhalable particles that affect lungs and breathing',
            'sources': 'Dust, pollen, construction, road dust',
            'health_effects': 'Lung irritation, reduced lung function'
        },
        'o3': {
            'description': 'Ground-level ozone formed by chemical reactions',
            'sources': 'Vehicle emissions, industrial facilities, gasoline vapors',
            'health_effects': 'Chest pain, coughing, throat irritation'
        },
        'no2': {
            'description': 'Nitrogen dioxide from combustion processes',
            'sources': 'Cars, trucks, buses, power plants',
            'health_effects': 'Respiratory infections, asthma aggravation'
        },
        'so2': {
            'description': 'Sulfur dioxide from fossil fuel combustion',
            'sources': 'Coal and oil burning, metal smelting',
            'health_effects': 'Breathing problems, lung damage'
        },
        'co': {
            'description': 'Carbon monoxide from incomplete combustion',
            'sources': 'Vehicle exhaust, heating systems, stoves',
            'health_effects': 'Reduces oxygen delivery to organs'
        }
    }
    
    pollutants = []
    for p in pollutants_data:
        conc = p.get('concentration', {}) or {}
        conc_val = conc.get('value') if isinstance(conc, dict) else conc
        conc_units = conc.get('units') if isinstance(conc, dict) else None
        pollutant_aqi = (p.get('additionalInfo', {}) or {}).get('aqi') if p.get('additionalInfo') else p.get('aqi')
        
        # Get health category for this pollutant's AQI
        pollutant_category = "Good"
        if pollutant_aqi:
            for (lower_bound, upper_bound), (cat, _, _, _) in aqi_categories.items():
                if lower_bound <= pollutant_aqi <= upper_bound:
                    pollutant_category = cat
                    break
        
        code = p.get('code', '').lower()
        health_info = pollutant_health_info.get(code, {})
        
        pollutants.append({
            "name": p.get('displayName') or p.get('code'),
            "code": code,
            "aqi": pollutant_aqi,
            "category": pollutant_category,
            "concentration": f"{conc_val} {conc_units or ''}".strip(),
            "description": health_info.get('description', 'Air pollutant'),
            "sources": health_info.get('sources', 'Various sources'),
            "health_effects": health_info.get('health_effects', 'May affect health')
        })
    
    # Find dominant pollutant by highest AQI from the processed pollutants
    dominant_pollutant_name = "Unknown"
    dominant_pollutant_description = "No dominant pollutant identified."
    
    if pollutants:
        # Find the pollutant with the highest AQI from our processed list
        valid_pollutants = [p for p in pollutants if p.get('aqi') is not None and p.get('aqi') > 0]
        if valid_pollutants:
            dominant_pollutant = max(valid_pollutants, key=lambda p: p.get('aqi', 0))
            dominant_pollutant_name = dominant_pollutant.get('name', 'Unknown')
            dominant_pollutant_description = dominant_pollutant.get('description', 'This is the pollutant with the highest concentration in the air right now.')
        else:
            # If no valid AQI values, just take the first pollutant
            dominant_pollutant_name = pollutants[0].get('name', 'Unknown')
            dominant_pollutant_description = pollutants[0].get('description', 'Primary air pollutant in this area.')
    
    # If still unknown, try using the dominant_pollutant_code from the raw data
    if dominant_pollutant_name == "Unknown" and dominant_pollutant_code:
        for p in pollutants_data:
            if p.get('code', '').lower() == dominant_pollutant_code.lower():
                dominant_pollutant_name = p.get('displayName', p.get('code', 'Unknown'))
                code = p.get('code', '').lower()
                health_info = pollutant_health_info.get(code, {})
                dominant_pollutant_description = health_info.get('description', 'This is the pollutant with the highest concentration in the air right now.')
                break
    
    # Final fallback: if we have pollutants but still no dominant identified
    if dominant_pollutant_name == "Unknown" and pollutants_data:
        # Just take the first pollutant from the data
        first_pollutant = pollutants_data[0]
        dominant_pollutant_name = first_pollutant.get('displayName', first_pollutant.get('code', 'PM2.5'))
        code = first_pollutant.get('code', 'pm25').lower()
        health_info = pollutant_health_info.get(code, {})
        dominant_pollutant_description = health_info.get('description', 'Primary air pollutant detected in this area.')
    
    # Absolute final fallback - if everything else fails, set to PM2.5
    if dominant_pollutant_name == "Unknown":
        dominant_pollutant_name = "PM2.5"
        dominant_pollutant_description = "Fine particles that can penetrate deep into lungs and bloodstream"

    formatted_data = {
        "overview": {
            "aqi": overall_aqi,
            "category": category,
            "dominant_pollutant": dominant_pollutant_name,
            "dominant_pollutant_description": dominant_pollutant_description,
            "health_summary": health_summary,
            "data_source": aqi_data.get('provider', 'unknown'),
            "location": aqi_data.get('city') if aqi_data.get('city') else 'Location data unavailable',
            "last_updated": aqi_data.get('time', {}).get('s') if aqi_data.get('time') else 'Real-time estimate'
        },
        "recommendations": {
            "general_population": general_rec,
            "sensitive_groups": sensitive_rec
        },
        "pollutants": pollutants,
        "pollutant_count": len(pollutants)
    }
    return formatted_data

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

def classify_query_type(user_prompt):
    """Classify the type of user query to determine how to handle it."""
    prompt_lower = user_prompt.lower()
    
    # General AQI questions
    if any(phrase in prompt_lower for phrase in ['what is aqi', 'what does aqi mean', 'explain aqi', 'air quality index']):
        return 'aqi_explanation'
    
    # Health condition questions
    if any(phrase in prompt_lower for phrase in ['asthma', 'copd', 'heart condition', 'respiratory', 'pregnant', 'elderly', 'child']):
        return 'health_advice'
    
    # Trend questions
    if any(phrase in prompt_lower for phrase in ['trend', 'getting better', 'getting worse', 'improving', 'forecast']):
        return 'trend_analysis'
    
    # General air quality questions
    if any(phrase in prompt_lower for phrase in ['how to protect', 'what should i do', 'safety tips', 'recommendations']):
        return 'general_advice'
    
    # Location-specific queries (default)
    return 'location_query'

def handle_general_questions(query_type, user_prompt):
    """Handle general questions that don't require location-specific data."""
    
    if query_type == 'aqi_explanation':
        return {
            "type": "educational",
            "title": "What is the Air Quality Index (AQI)?",
            "content": {
                "overview": {
                    "definition": "The Air Quality Index (AQI) is a number used to communicate how polluted the air currently is or how polluted it is forecast to become.",
                    "scale": "AQI values range from 0 to 500, where higher values indicate greater health concerns.",
                    "purpose": "It helps you understand what local air quality means to your health."
                },
                "categories": [
                    {"range": "0-50", "level": "Good", "color": "Green", "description": "Air quality is satisfactory, and air pollution poses little or no risk."},
                    {"range": "51-100", "level": "Moderate", "color": "Yellow", "description": "Air quality is acceptable for most people, though sensitive individuals may experience minor issues."},
                    {"range": "101-150", "level": "Unhealthy for Sensitive Groups", "color": "Orange", "description": "Members of sensitive groups may experience health effects."},
                    {"range": "151-200", "level": "Unhealthy", "color": "Red", "description": "Everyone may begin to experience health effects."},
                    {"range": "201-300", "level": "Very Unhealthy", "color": "Purple", "description": "Health alert: everyone may experience more serious health effects."},
                    {"range": "301-500", "level": "Hazardous", "color": "Maroon", "description": "Emergency conditions: everyone is more likely to be affected."}
                ],
                "pollutants": {
                    "description": "AQI is calculated based on five major pollutants:",
                    "list": ["PM2.5 (fine particles)", "PM10 (coarse particles)", "Ozone (O₃)", "Nitrogen Dioxide (NO₂)", "Sulfur Dioxide (SO₂)", "Carbon Monoxide (CO)"]
                }
            }
        }
    
    elif query_type == 'health_advice':
        health_conditions = {
            'asthma': {
                'condition': 'Asthma',
                'general_advice': 'People with asthma should be especially careful during poor air quality days.',
                'recommendations': [
                    'Keep rescue inhalers accessible at all times',
                    'Monitor AQI daily and limit outdoor activities when levels are unhealthy',
                    'Consider wearing N95 masks during high pollution days',
                    'Keep windows closed and use air purifiers indoors',
                    'Take medications as prescribed by your doctor'
                ],
                'warning_signs': ['Increased coughing', 'Shortness of breath', 'Chest tightness', 'Wheezing']
            },
            'heart condition': {
                'condition': 'Heart Disease',
                'general_advice': 'Air pollution can increase the risk of heart attacks and other cardiovascular problems.',
                'recommendations': [
                    'Avoid outdoor exercise during high pollution days',
                    'Take medications as prescribed',
                    'Monitor for symptoms like chest pain or unusual fatigue',
                    'Consider indoor activities when AQI > 100',
                    'Consult your doctor about air quality concerns'
                ],
                'warning_signs': ['Chest pain', 'Unusual fatigue', 'Shortness of breath', 'Irregular heartbeat']
            },
            'copd': {
                'condition': 'COPD (Chronic Obstructive Pulmonary Disease)',
                'general_advice': 'COPD patients are highly sensitive to air pollution and should take extra precautions.',
                'recommendations': [
                    'Stay indoors when AQI exceeds 100',
                    'Use prescribed medications regularly',
                    'Consider oxygen therapy if recommended by doctor',
                    'Avoid areas with heavy traffic or industrial pollution',
                    'Use air purifiers and keep indoor air clean'
                ],
                'warning_signs': ['Increased breathlessness', 'More frequent coughing', 'Changes in mucus color', 'Fatigue']
            }
        }
        
        # Determine which condition is mentioned
        condition_key = 'general'
        for key in health_conditions.keys():
            if key in user_prompt.lower():
                condition_key = key
                break
        
        if condition_key in health_conditions:
            condition_info = health_conditions[condition_key]
            return {
                "type": "health_advice",
                "title": f"Air Quality Advice for {condition_info['condition']}",
                "content": condition_info
            }
        else:
            return {
                "type": "health_advice",
                "title": "General Health Advice for Air Quality",
                "content": {
                    'condition': 'General Population',
                    'general_advice': 'Everyone should be aware of air quality levels and take appropriate precautions.',
                    'recommendations': [
                        'Check daily AQI forecasts',
                        'Limit outdoor activities when AQI > 150',
                        'Exercise indoors during poor air quality days',
                        'Keep windows closed during high pollution periods',
                        'Consider air purifiers for your home'
                    ],
                    'sensitive_groups': ['Children', 'Elderly (65+)', 'Pregnant women', 'People with heart/lung conditions']
                }
            }
    
    elif query_type == 'general_advice':
        return {
            "type": "general_advice",
            "title": "Air Quality Protection Tips",
            "content": {
                "indoor_tips": [
                    "Keep windows and doors closed during high pollution days",
                    "Use air purifiers with HEPA filters",
                    "Avoid using candles, fireplaces, or gas stoves",
                    "Keep indoor plants that help purify air",
                    "Vacuum regularly with HEPA filter"
                ],
                "outdoor_tips": [
                    "Check AQI before going outside",
                    "Wear N95 or P100 masks when AQI > 150",
                    "Avoid exercising outdoors during poor air quality",
                    "Stay away from busy roads during rush hour",
                    "Plan outdoor activities during early morning or late evening"
                ],
                "when_to_be_concerned": [
                    "AQI consistently above 100 for your area",
                    "Visible smog or haze",
                    "Burning smell in the air",
                    "Respiratory symptoms increasing",
                    "Local air quality alerts issued"
                ]
            }
        }
    
    return None

@app.route('/api/query', methods=['POST'])
def handle_query():
    """Main endpoint to handle user's natural language queries."""
    if not llm:
        return jsonify({"error": "LLM not configured. Check your GOOGLE_API_KEY."}), 500

    data = request.get_json()
    user_prompt = data.get('prompt')

    if not user_prompt:
        return jsonify({"error": "Prompt is required"}), 400

    try:
        # First, classify the type of query
        query_type = classify_query_type(user_prompt)
        
        # Handle general questions that don't need location data
        if query_type in ['aqi_explanation', 'health_advice', 'general_advice']:
            general_response = handle_general_questions(query_type, user_prompt)
            if general_response:
                return jsonify({
                    "explanation": general_response,
                    "coordinates": None,
                    "raw_aqi_data": None
                })
        
        # For location-specific queries, proceed with location extraction
        if not MAPS_API_KEY:
            return jsonify({"error": "Google Maps API key not configured."}), 500
            
        location_extraction_prompt = f"Extract only the city and country from the following text, in the format 'City, Country'. If a specific city is not mentioned, identify the most likely major city based on the context. Text: '{user_prompt}'"
        location_response = llm.generate_content(location_extraction_prompt)
        location_name = location_response.text.strip()

        if not location_name or "could not" in location_name.lower():
            return jsonify({"error": "Could not identify a location from your query. Please specify a city or location."}), 400

        coordinates = get_lat_lng(location_name)
        if not coordinates:
            return jsonify({"error": f"Could not find coordinates for '{location_name}'"}), 404

        aqi_data = get_air_quality(coordinates['lat'], coordinates['lng'])
        if not aqi_data:
            return jsonify({"error": "Could not retrieve air quality data for the location."}), 500

        explanation_json = format_air_quality_data(aqi_data)
        
        # Add location name to the response
        explanation_json["location_name"] = location_name

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
    """Provides location-specific forecast data."""
    data = request.get_json()
    lat = data.get('lat')
    lng = data.get('lng')
    
    if not lat or not lng:
        return jsonify({"error": "Latitude and longitude are required"}), 400
    
    # Get current AQI as baseline
    current_aqi_data = get_air_quality(lat, lng)
    base_aqi = current_aqi_data.get('aqi', 50) if current_aqi_data else 50
    
    # Generate realistic forecast based on location and current conditions
    forecast_values = []
    for i in range(7):
        # Add some variation based on day and location
        day_variation = (hash(f"{lat}_{lng}_{i}") % 30) - 15  # ±15 variation
        seasonal_trend = 5 * (i - 3) / 3  # slight trend over week
        forecast_aqi = max(10, min(200, int(base_aqi + day_variation + seasonal_trend)))
        forecast_values.append(forecast_aqi)
    
    forecast_data = {
        "labels": ["Today", "Tomorrow", "Day 3", "Day 4", "Day 5", "Day 6", "Day 7"],
        "datasets": [
            {
                "label": "Predicted AQI",
                "data": forecast_values,
                "fill": False,
                "borderColor": 'rgb(75, 192, 192)',
                "backgroundColor": 'rgba(75, 192, 192, 0.2)',
                "tension": 0.1
            }
        ]
    }
    return jsonify(forecast_data)

@app.route('/api/heatmap-data', methods=['POST'])
def get_heatmap_data():
    """Creates a dense global pollution heatmap using estimated and real data."""
    if not MAPS_API_KEY:
        print("ERROR: GOOGLE_MAPS_API_KEY not configured for heatmap")
        return jsonify({"error": "Google Maps API key not configured."}), 500
    # Accept viewport bounds in the POST body to return points only within view
    try:
        body = request.get_json(silent=True) or {}
    except Exception:
        body = {}

    sw = body.get('sw')
    ne = body.get('ne')
    # Allow client to request a maximum number of points (sensible default)
    max_points = int(body.get('max_points', 1500))

    heatmap_points = []

    # If bounds provided, generate grid only within bounds (adaptive step)
    if sw and ne:
        try:
            lat_min = float(sw.get('lat'))
            lng_min = float(sw.get('lng'))
            lat_max = float(ne.get('lat'))
            lng_max = float(ne.get('lng'))

            # normalize in case of reversed coordinates
            if lat_min > lat_max:
                lat_min, lat_max = lat_max, lat_min

            # handle antimeridian crossing for longitude
            crosses_antimeridian = False
            if lng_min <= lng_max:
                lng_span = lng_max - lng_min
            else:
                crosses_antimeridian = True
                lng_span = (180 - lng_min) + (lng_max + 180)

            # Target ~30x30 grid within viewport; clamp steps to 0.25-8 degrees
            target_cells = 30.0
            lat_step = max(0.25, min(8.0, (lat_max - lat_min) / target_cells if (lat_max - lat_min) > 0 else 1.0))
            lng_step = max(0.25, min(8.0, lng_span / target_cells if lng_span > 0 else 1.0))

            # Iterate latitude
            lat = lat_min
            while lat <= lat_max:
                # iterate longitude, taking antimeridian into account
                if not crosses_antimeridian:
                    lng = lng_min
                    while lng <= lng_max:
                        actual_lat = max(-85, min(85, lat + (hash(f"{lat}_{lng}") % 3 - 1)))
                        actual_lng = lng + (hash(f"{lng}_{lat}") % 4 - 2)
                        if actual_lng > 180:
                            actual_lng -= 360
                        if actual_lng < -180:
                            actual_lng += 360

                        estimated_aqi = estimate_pollution_by_location(actual_lat, actual_lng)
                        heatmap_points.append({
                            "lat": actual_lat,
                            "lng": actual_lng,
                            "aqi": estimated_aqi,
                            "weight": estimated_aqi,
                            "estimated": True
                        })
                        lng += lng_step
                else:
                    # two ranges: lng_min..180 and -180..lng_max
                    lng = lng_min
                    while lng <= 180:
                        actual_lat = max(-85, min(85, lat + (hash(f"{lat}_{lng}") % 3 - 1)))
                        actual_lng = lng + (hash(f"{lng}_{lat}") % 4 - 2)
                        estimated_aqi = estimate_pollution_by_location(actual_lat, actual_lng)
                        heatmap_points.append({
                            "lat": actual_lat,
                            "lng": actual_lng,
                            "aqi": estimated_aqi,
                            "weight": estimated_aqi,
                            "estimated": True
                        })
                        lng += lng_step
                    lng = -180
                    while lng <= lng_max:
                        actual_lat = max(-85, min(85, lat + (hash(f"{lat}_{lng}") % 3 - 1)))
                        actual_lng = lng + (hash(f"{lng}_{lat}") % 4 - 2)
                        estimated_aqi = estimate_pollution_by_location(actual_lat, actual_lng)
                        heatmap_points.append({
                            "lat": actual_lat,
                            "lng": actual_lng,
                            "aqi": estimated_aqi,
                            "weight": estimated_aqi,
                            "estimated": True
                        })
                        lng += lng_step

                lat += lat_step

        except Exception as e:
            print(f"Error generating bounded heatmap: {e}")

        # Add some real data points inside bounds to improve accuracy
        real_data_points = get_limited_real_data()
        # Filter real data by bounds
        def in_bounds(p):
            plat, plng = p.get('lat'), p.get('lng')
            if lat_min <= plat <= lat_max:
                if not crosses_antimeridian:
                    return lng_min <= plng <= lng_max
                else:
                    return plng >= lng_min or plng <= lng_max
            return False

        real_in_bounds = [p for p in real_data_points if in_bounds(p)]
        heatmap_points.extend(real_in_bounds)

    else:
        # No bounds provided: generate a coarse global grid but cap size
        lat_step = 6
        lng_step = 8
        for lat in range(-80, 81, lat_step):
            for lng in range(-180, 181, lng_step):
                actual_lat = lat + (hash(f"{lat}_{lng}") % 3 - 1)
                actual_lng = lng + (hash(f"{lng}_{lat}") % 4 - 2)
                actual_lat = max(-85, min(85, actual_lat))
                actual_lng = max(-180, min(180, actual_lng))
                estimated_aqi = estimate_pollution_by_location(actual_lat, actual_lng)
                heatmap_points.append({
                    "lat": actual_lat,
                    "lng": actual_lng,
                    "aqi": estimated_aqi,
                    "weight": estimated_aqi,
                    "estimated": True
                })

        heatmap_points.extend(get_limited_real_data())

    # Downsample deterministically if too many points
    total = len(heatmap_points)
    if total > max_points:
        sampled = []
        step = float(total) / float(max_points)
        i = 0.0
        while len(sampled) < max_points and int(i) < total:
            sampled.append(heatmap_points[int(i)])
            i += step
        heatmap_points = sampled

    print(f"Returning {len(heatmap_points)} heatmap points (requested max {max_points})")
    return jsonify(heatmap_points)

def estimate_pollution_by_location(lat, lng):
    """Estimate pollution levels based on geographic location and known patterns."""
    
    # Base pollution level (clean air)
    base_pollution = 20
    
    # Ocean areas - very clean
    if is_ocean_area(lat, lng):
        return 15 + (hash(f"{lat}{lng}") % 10)  # 15-25 range
    
    # Polar regions - very clean
    if abs(lat) > 65:
        return 10 + (hash(f"{lat}{lng}") % 8)  # 10-18 range
    
    pollution_hotspots = [
    # China industrial belt
    {"center": (39.9042, 116.4074), "radius": 15, "pollution": 120},  # Beijing
    {"center": (31.2304, 121.4737), "radius": 15, "pollution": 130},  # Shanghai
    {"center": (23.1291, 113.2644), "radius": 12, "pollution": 110},  # Guangzhou
    {"center": (30.5728, 104.0668), "radius": 12, "pollution": 115},  # Chengdu
    {"center": (22.5431, 114.0579), "radius": 12, "pollution": 125},  # Shenzhen

    # India industrial areas
    {"center": (28.6139, 77.2090), "radius": 10, "pollution": 150},  # Delhi
    {"center": (19.0760, 72.8777), "radius": 10, "pollution": 140},  # Mumbai
    {"center": (22.5726, 88.3639), "radius": 10, "pollution": 145},  # Kolkata
    {"center": (13.0827, 80.2707), "radius": 10, "pollution": 135},  # Chennai
    {"center": (12.9716, 77.5946), "radius": 10, "pollution": 130},  # Bengaluru

    # Middle East
    {"center": (29.3759, 47.9774), "radius": 8, "pollution": 100},   # Kuwait City
    {"center": (25.276987, 55.296249), "radius": 8, "pollution": 95}, # Dubai
    {"center": (21.4225, 39.8262), "radius": 8, "pollution": 90},    # Jeddah
    {"center": (31.9686, 99.9018), "radius": 8, "pollution": 85},    # Riyadh
    {"center": (26.8206, 30.8025), "radius": 8, "pollution": 80},    # Cairo

    # Europe industrial
    {"center": (51.1657, 10.4515), "radius": 12, "pollution": 70},   # Germany
    {"center": (48.8566, 2.3522), "radius": 12, "pollution": 65},    # Paris
    {"center": (51.5074, -0.1278), "radius": 12, "pollution": 60},   # London
    {"center": (52.3791, 4.9009), "radius": 12, "pollution": 55},    # Amsterdam
    {"center": (41.9028, 12.4964), "radius": 12, "pollution": 50},   # Rome

    # North America
    {"center": (34.0522, -118.2437), "radius": 8, "pollution": 80},  # Los Angeles
    {"center": (40.7128, -74.0060), "radius": 8, "pollution": 75},   # New York City
    {"center": (41.8781, -87.6298), "radius": 8, "pollution": 70},   # Chicago
    {"center": (29.7604, -95.3698), "radius": 8, "pollution": 65},   # Houston
    {"center": (37.7749, -122.4194), "radius": 8, "pollution": 60},  # San Francisco

    # Other regions
    {"center": (-23.5505, -46.6333), "radius": 8, "pollution": 85},  # São Paulo
    {"center": (35.6762, 139.6503), "radius": 10, "pollution": 75},  # Tokyo
    {"center": (-33.8688, 151.2093), "radius": 8, "pollution": 65},  # Sydney
    {"center": (39.9042, 32.8597), "radius": 8, "pollution": 60},   # Ankara
    {"center": (55.7558, 37.6173), "radius": 8, "pollution": 55},   # Moscow
]

    
    max_pollution = base_pollution
    
    # Check proximity to pollution hotspots
    for hotspot in pollution_hotspots:
        center_lat, center_lng = hotspot["center"]
        distance = ((lat - center_lat) ** 2 + (lng - center_lng) ** 2) ** 0.5
        
        if distance < hotspot["radius"]:
            # Calculate pollution based on distance from center
            influence = max(0, 1 - (distance / hotspot["radius"]))
            pollution_contribution = hotspot["pollution"] * influence
            max_pollution = max(max_pollution, base_pollution + pollution_contribution)
    
    # Add some randomness for natural variation
    variation = (hash(f"{lat}_{lng}_var") % 20) - 10
    final_pollution = max(5, min(200, int(max_pollution + variation)))
    
    return final_pollution

def is_ocean_area(lat, lng):
    """Determine if coordinates are likely over ocean."""
    # Simplified ocean detection based on major landmasses
    
    # Pacific Ocean
    if lng < -120 or lng > 150:
        if not (lat > 50 and lng > -130 and lng < -100):  # Exclude North America west coast
            return True
    
    # Atlantic Ocean
    if lng > -60 and lng < 20 and (lat > 50 or lat < -20):
        return True
    
    # Indian Ocean  
    if lng > 60 and lng < 120 and lat < -10:
        return True
    
    # Arctic Ocean
    if lat > 70:
        return True
    
    # Antarctic Ocean
    if lat < -60:
        return True
        
    return False

def get_limited_real_data():
    """Get a small amount of real API data for key locations."""
    real_points = []
    
    # Key cities where we want real data (limit to 10 to avoid quota issues)
    key_locations = [
        {"lat": 40.7128, "lng": -74.0060},  # NYC
        {"lat": 34.0522, "lng": -118.2437}, # LA
        {"lat": 51.5074, "lng": -0.1278},   # London
        {"lat": 48.8566, "lng": 2.3522},    # Paris
        {"lat": 35.6762, "lng": 139.6503},  # Tokyo
    ]
    
    for location in key_locations:
        try:
            aqi_data = get_air_quality(location["lat"], location["lng"])
            # aqi_data is the unified structure returned by get_air_quality
            if aqi_data and aqi_data.get('aqi') is not None:
                aqi_value = aqi_data.get('aqi') or 0
                try:
                    aqi_value_num = int(aqi_value)
                except Exception:
                    aqi_value_num = 0
                if aqi_value_num > 0:
                    real_points.append({
                        "lat": location["lat"],
                        "lng": location["lng"],
                        "aqi": aqi_value_num,
                        "weight": aqi_value_num,
                        "estimated": False
                    })
        except:
            pass  # Skip if API call fails
    
    return real_points




if __name__ == '__main__':
    app.run(debug=True, port=5000)