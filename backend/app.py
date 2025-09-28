import os
from flask import Flask, request, jsonify
import google.generativeai as genai
import requests
from dotenv import load_dotenv
import json
import traceback
from functools import wraps
from jose import jwt, JWTError
from urllib.request import urlopen
from flask_cors import CORS

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

# Auth0 Configuration
AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN")
AUTH0_API_AUDIENCE = os.environ.get("AUTH0_API_AUDIENCE")
AUTH0_ALGORITHMS = ["RS256"]

# Auth0 Configuration
AUTH0_DOMAIN = os.environ.get('AUTH0_DOMAIN')
AUTH0_API_AUDIENCE = os.environ.get('AUTH0_API_AUDIENCE')
ALGORITHMS = ["RS256"]

# --- Flask App Initialization ---
app = Flask(__name__)
CORS(app)  # Enable CORS for all domains

# In-memory user profiles storage (in production, use a database)
user_profiles = {}

# --- Auth0 Helper Functions ---

def get_token_auth_header():
    """Obtains the Access Token from the Authorization Header"""
    auth = request.headers.get("Authorization", None)
    print(f"Authorization header: {auth}")  # Debug
    if not auth:
        print("No Authorization header found")  # Debug
        return None
    
    parts = auth.split()
    if parts[0].lower() != "bearer":
        print(f"Invalid auth type: {parts[0]}")  # Debug
        return None
    elif len(parts) == 1:
        print("No token found in Authorization header")  # Debug
        return None
    elif len(parts) > 2:
        print("Malformed Authorization header")  # Debug
        return None
    
    token = parts[1]
    print(f"Token extracted: {token[:20]}...")  # Debug (first 20 chars)
    return token

def verify_decode_jwt(token):
    """Verifies and decodes the JWT token"""
    print(f"AUTH0_DOMAIN: {AUTH0_DOMAIN}")  # Debug
    print(f"AUTH0_API_AUDIENCE: {AUTH0_API_AUDIENCE}")  # Debug
    
    if not AUTH0_DOMAIN or not AUTH0_API_AUDIENCE:
        print("Missing AUTH0_DOMAIN or AUTH0_API_AUDIENCE")  # Debug
        return None
        
    try:
        # Use requests library for better SSL handling
        response = requests.get(f"https://{AUTH0_DOMAIN}/.well-known/jwks.json")
        response.raise_for_status()
        jwks = response.json()
        print("Successfully fetched JWKS")  # Debug
        
        unverified_header = jwt.get_unverified_header(token)
        print(f"Token header: {unverified_header}")  # Debug
        rsa_key = {}
        
        for key in jwks["keys"]:
            if key["kid"] == unverified_header["kid"]:
                rsa_key = {
                    "kty": key["kty"],
                    "kid": key["kid"],
                    "use": key["use"],
                    "n": key["n"],
                    "e": key["e"]
                }
                break
        
        if rsa_key:
            print("RSA key found, attempting to decode...")  # Debug
            payload = jwt.decode(
                token,
                rsa_key,
                algorithms=AUTH0_ALGORITHMS,
                audience=AUTH0_API_AUDIENCE,
                issuer=f"https://{AUTH0_DOMAIN}/"
            )
            print(f"Token decoded successfully: {payload.get('sub')}")  # Debug
            return payload
        else:
            print("No matching RSA key found")  # Debug
    except JWTError as e:
        print(f"JWT Error: {e}")  # Debug
        return None
    except Exception as e:
        print(f"General error in JWT verification: {e}")  # Debug
        return None
    
    return None

def get_user_from_token():
    """Extract user info from token if present"""
    token = get_token_auth_header()
    if not token:
        return None
    
    payload = verify_decode_jwt(token)
    print(f"JWT payload: {payload}")  # Debug
    if payload:
        return {
            'user_id': payload.get('sub'),
            'email': payload.get('email'),
            'name': payload.get('name'),
            'picture': payload.get('picture'),
            'user_metadata': payload.get('https://respire-app.com/user_metadata', {})  # Custom claim
        }
    return None

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

@app.route('/api/test-auth', methods=['GET'])
def test_auth():
    """Test authentication and show token info"""
    user = get_user_from_token()
    return jsonify({
        'authenticated': user is not None,
        'user': user,
        'headers': dict(request.headers)
    })

@app.route('/api/user/profile', methods=['GET'])
def get_user_profile():
    """Get user profile data."""
    user = get_user_from_token()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    
    user_id = user['user_id']
    profile = user_profiles.get(user_id, {
        'age': None,
        'medical_conditions': [],
        'allergies': [],
        'medications': [],
        'activity_level': 'moderate',
        'location': None
    })
    
    return jsonify({
        'user_info': user,
        'profile': profile
    })

@app.route('/api/user/profile', methods=['POST'])
def update_user_profile():
    """Update user profile data."""
    print(f"POST /api/user/profile - Headers: {dict(request.headers)}")  # Debug
    user = get_user_from_token()
    print(f"User from token: {user}")  # Debug
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    
    data = request.get_json()
    user_id = user['user_id']
    
    # Validate and sanitize input
    profile = {
        'age': data.get('age'),
        'medical_conditions': data.get('medical_conditions', []),
        'allergies': data.get('allergies', []),
        'medications': data.get('medications', []),
        'activity_level': data.get('activity_level', 'moderate'),
        'location': data.get('location')
    }
    
    # Validate age
    if profile['age'] is not None:
        try:
            age = int(profile['age'])
            if age < 0 or age > 120:
                return jsonify({"error": "Invalid age"}), 400
            profile['age'] = age
        except (ValueError, TypeError):
            return jsonify({"error": "Age must be a number"}), 400
    
    # Validate activity level
    valid_activity_levels = ['low', 'moderate', 'high', 'very_high']
    if profile['activity_level'] not in valid_activity_levels:
        profile['activity_level'] = 'moderate'
    
    user_profiles[user_id] = profile
    
    return jsonify({
        'message': 'Profile updated successfully',
        'profile': profile
    })

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
    
    # Check if query is outside air quality domain
    out_of_domain_keywords = [
        'weather', 'temperature', 'rain', 'snow', 'storm', 'hurricane', 'tornado',
        'cooking', 'recipe', 'food', 'restaurant', 'diet', 'nutrition',
        'sports scores', 'news', 'politics', 'election', 'stock', 'finance',
        'movie', 'music', 'entertainment', 'celebrity', 'travel booking',
        'shopping', 'price', 'buy', 'sell', 'product review',
        'programming', 'code', 'software', 'app development',
        'relationship', 'dating', 'marriage', 'family problems',
        'legal advice', 'lawyer', 'court', 'lawsuit',
        'homework', 'essay', 'assignment', 'school project'
    ]
    
    # Air quality related keywords - comprehensive coverage
    air_quality_keywords = [
        'air', 'pollution', 'aqi', 'quality', 'smog', 'haze', 'particulate',
        'pm2.5', 'pm10', 'ozone', 'o3', 'nitrogen', 'sulfur', 'carbon monoxide',
        'pollutant', 'emission', 'breathing', 'respiratory', 'lung',
        'asthma', 'copd', 'allergy', 'pollen', 'dust', 'mold', 'indoor air',
        'air purifier', 'filter', 'ventilation', 'hvac', 'clean air',
        'toxic', 'hazardous', 'unhealthy', 'wildfire', 'smoke', 'industrial',
        # Additional comprehensive keywords
        'atmosphere', 'atmospheric', 'environment', 'environmental', 'contamination',
        'contaminant', 'aerosol', 'particle', 'particles', 'fine particles',
        'coarse particles', 'visibility', 'visibility reduction', 'air index',
        'air monitoring', 'air sensor', 'air measurement', 'air data',
        'clean', 'dirty', 'fresh', 'stale', 'stuffy', 'breathable',
        'health', 'healthy', 'safe', 'safety', 'dangerous', 'harmful',
        'good air', 'bad air', 'poor air', 'excellent air', 'moderate air',
        'sensitive groups', 'vulnerable', 'exposure', 'inhale', 'inhalation',
        'outdoor air', 'ambient air', 'surrounding air', 'local air'
    ]
    
    # Check for out-of-domain queries first
    if any(keyword in prompt_lower for keyword in out_of_domain_keywords):
        # But still allow if it's air quality related
        if not any(keyword in prompt_lower for keyword in air_quality_keywords):
            return 'out_of_domain'
    
    # Pollutant-specific questions
    if any(phrase in prompt_lower for phrase in ['ozone', 'o3', 'ground level ozone', 'tropospheric ozone']):
        return 'ozone_questions'
    
    if any(phrase in prompt_lower for phrase in ['pm2.5', 'pm 2.5', 'fine particles', 'particulate matter']):
        return 'particulate_questions'
    
    if any(phrase in prompt_lower for phrase in ['nitrogen dioxide', 'no2', 'nitrogen oxide']):
        return 'nitrogen_questions'
    
    if any(phrase in prompt_lower for phrase in ['sulfur dioxide', 'so2', 'sulfur']):
        return 'sulfur_questions'
    
    if any(phrase in prompt_lower for phrase in ['carbon monoxide', 'co', 'carbon']):
        return 'carbon_monoxide_questions'
    
    # Allergy and pollen questions
    if any(phrase in prompt_lower for phrase in ['pollen', 'allergy', 'allergies', 'hay fever', 'seasonal allergy', 'allergic reaction']):
        return 'allergy_pollen_advice'
    
    # Indoor air quality questions
    if any(phrase in prompt_lower for phrase in ['indoor air', 'home air', 'house air', 'air purifier', 'hvac', 'ventilation']):
        return 'indoor_air_advice'
    
    # Wildfire and smoke questions
    if any(phrase in prompt_lower for phrase in ['wildfire', 'forest fire', 'smoke', 'fire smoke', 'ash']):
        return 'wildfire_smoke_advice'
    
    # General AQI questions - much broader coverage
    if any(phrase in prompt_lower for phrase in [
        'what is aqi', 'what does aqi mean', 'explain aqi', 'air quality index', 'aqi scale',
        'aqi range', 'good aqi', 'bad aqi', 'safe aqi', 'healthy aqi', 'aqi level', 'aqi value',
        'what is a good aqi', 'what is safe aqi', 'normal aqi', 'acceptable aqi', 'aqi guidelines',
        'aqi standards', 'aqi categories', 'aqi meaning', 'how to read aqi', 'understand aqi'
    ]):
        return 'aqi_explanation'
    
    # Health condition questions
    if any(phrase in prompt_lower for phrase in ['asthma', 'copd', 'heart condition', 'respiratory', 'pregnant', 'elderly', 'child']):
        return 'health_advice'
    
    # Protective measures and safety
    if any(phrase in prompt_lower for phrase in ['mask', 'n95', 'protection', 'how to protect', 'what should i do', 'safety tips', 'recommendations']):
        return 'protection_advice'
    
    # Exercise and outdoor activity questions
    if any(phrase in prompt_lower for phrase in ['exercise', 'running', 'jogging', 'outdoor activity', 'sports', 'workout']):
        return 'exercise_advice'
    
    # Trend and forecast questions
    if any(phrase in prompt_lower for phrase in ['trend', 'getting better', 'getting worse', 'improving', 'forecast', 'prediction']):
        return 'trend_analysis'
    
    # General air quality questions - catch common patterns
    if any(phrase in prompt_lower for phrase in [
        'what is good', 'what is bad', 'what is safe', 'what is healthy', 'what is normal',
        'how much', 'how many', 'what level', 'what range', 'what value',
        'is it safe', 'is it healthy', 'is it dangerous', 'is it harmful',
        'should i worry', 'should i be concerned', 'is this normal', 'is this good',
        'what does this mean', 'what does it mean', 'explain this', 'tell me about',
        'how bad is', 'how good is', 'how safe is', 'how dangerous is'
    ]) and any(keyword in prompt_lower for keyword in ['air', 'aqi', 'pollution', 'quality', 'ozone', 'pm', 'particulate']):
        return 'general_air_quality'
    
    # Location-specific queries - simpler logic that allows direct location queries
    # First check for obvious location patterns with prepositions
    if any(phrase in prompt_lower for phrase in ['air quality in ', 'aqi in ', 'pollution in ', 'air quality at ', 'aqi at ', 'pollution at ', 'air quality near ', 'aqi near ', 'pollution near ']):
        return 'location_query'
    
    # Check for common city names or location patterns (but not if it's clearly a general question)
    common_places = ['beijing', 'london', 'tokyo', 'paris', 'new york', 'york', 'los angeles', 'angeles', 'san francisco', 'francisco', 'chicago', 'boston', 'seattle', 'miami', 'dallas', 'houston', 'atlanta', 'denver', 'phoenix', 'detroit', 'toronto', 'vancouver', 'montreal', 'sydney', 'melbourne', 'mumbai', 'delhi', 'shanghai', 'hong kong', 'singapore', 'bangkok', 'jakarta', 'manila', 'seoul', 'osaka', 'cairo', 'lagos', 'nairobi', 'cape town', 'johannesburg', 'berlin', 'madrid', 'rome', 'amsterdam', 'brussels', 'vienna', 'prague', 'warsaw', 'stockholm', 'oslo', 'helsinki', 'dublin', 'moscow', 'istanbul', 'athens', 'lisbon', 'zurich', 'geneva', 'barcelona', 'milan', 'venice', 'florence']
    
    # If it's just a place name or simple location query, treat as location
    words = prompt_lower.split()
    if len(words) <= 3 and any(place in prompt_lower for place in common_places):
        return 'location_query'
    
    # More specific location patterns
    if any(phrase in prompt_lower for phrase in ['in ', 'at ', 'near ', 'around ']) and not any(phrase in prompt_lower for phrase in ['what is', 'how much', 'what level', 'what range', 'is it', 'should i']):
        return 'location_query'
    
    # If it contains air quality keywords but doesn't fit other categories
    if any(keyword in prompt_lower for keyword in air_quality_keywords):
        return 'general_air_quality'
    
    # Default to out of domain if nothing matches
    return 'out_of_domain'

def generate_personalized_recommendations(aqi_data, user_profile):
    """Generate personalized health recommendations based on user profile."""
    if not user_profile:
        return None
    
    age = user_profile.get('age')
    medical_conditions = user_profile.get('medical_conditions', [])
    allergies = user_profile.get('allergies', [])
    activity_level = user_profile.get('activity_level', 'moderate')
    
    aqi = aqi_data.get('aqi', 0)
    
    recommendations = {
        'title': 'Personalized Recommendations',
        'age_specific': [],
        'condition_specific': [],
        'activity_specific': [],
        'urgent_warnings': []
    }
    
    # Age-specific recommendations
    if age:
        if age < 18:
            recommendations['age_specific'].extend([
                'Children are more sensitive to air pollution due to developing lungs',
                'Limit outdoor sports and activities when AQI > 100',
                'Ensure you stay hydrated and take frequent breaks indoors'
            ])
            if aqi > 150:
                recommendations['urgent_warnings'].append('Avoid all outdoor activities when AQI exceeds 150')
        elif age >= 65:
            recommendations['age_specific'].extend([
                'Older adults are at higher risk for air pollution-related health effects',
                'Consider postponing outdoor activities when AQI > 100',
                'Keep rescue medications easily accessible'
            ])
            if aqi > 100:
                recommendations['urgent_warnings'].append('Stay indoors and keep windows closed when AQI > 100')
        elif 18 <= age < 65:
            if aqi > 150:
                recommendations['age_specific'].append('Reduce strenuous outdoor activities and consider indoor alternatives')
    
    # Medical condition-specific recommendations
    condition_recommendations = {
        'asthma': {
            'general': [
                'Keep your rescue inhaler with you at all times',
                'Consider pre-medicating before going outside if recommended by your doctor',
                'Monitor your symptoms closely and go indoors if they worsen'
            ],
            'aqi_thresholds': {
                50: ['Use air purifiers indoors and keep windows closed'],
                100: ['Limit outdoor activities and take frequent breaks indoors'],
                150: ['Avoid all outdoor activities and stay indoors with air conditioning']
            }
        },
        'copd': {
            'general': [
                'Use your prescribed medications as directed',
                'Consider oxygen therapy if recommended by your doctor',
                'Avoid areas with heavy traffic or industrial pollution'
            ],
            'aqi_thresholds': {
                50: ['Stay indoors during peak pollution hours'],
                100: ['Avoid all outdoor activities and keep windows closed'],
                150: ['Emergency action: Stay indoors, use air purifiers, contact doctor if symptoms worsen']
            }
        },
        'heart_disease': {
            'general': [
                'Monitor for chest pain, unusual fatigue, or shortness of breath',
                'Take medications as prescribed',
                'Avoid strenuous activities during high pollution days'
            ],
            'aqi_thresholds': {
                100: ['Consider indoor exercise alternatives'],
                150: ['Avoid all outdoor physical activity'],
                200: ['Stay indoors and contact your doctor if you experience cardiac symptoms']
            }
        },
        'diabetes': {
            'general': [
                'Air pollution can affect blood sugar control',
                'Monitor blood glucose more frequently during high pollution days',
                'Stay hydrated and take medications as prescribed'
            ],
            'aqi_thresholds': {
                100: ['Limit outdoor activities and monitor blood sugar closely'],
                150: ['Stay indoors and check blood glucose more frequently']
            }
        }
    }
    
    for condition in medical_conditions:
        condition_lower = condition.lower()
        if condition_lower in condition_recommendations:
            rec = condition_recommendations[condition_lower]
            recommendations['condition_specific'].extend(rec['general'])
            
            # Add AQI-specific recommendations
            for threshold in sorted(rec['aqi_thresholds'].keys()):
                if aqi >= threshold:
                    recommendations['condition_specific'].extend(rec['aqi_thresholds'][threshold])
    
    # Activity level recommendations
    activity_recommendations = {
        'low': {
            100: ['Gentle indoor activities are recommended'],
            150: ['Stay indoors and avoid any physical exertion']
        },
        'moderate': {
            100: ['Consider indoor exercise alternatives like yoga or light stretching'],
            150: ['Replace outdoor workouts with indoor activities']
        },
        'high': {
            50: ['Consider timing outdoor workouts for early morning or late evening'],
            100: ['Move intense workouts indoors or reschedule for cleaner air days'],
            150: ['Avoid all outdoor exercise and choose indoor fitness activities']
        },
        'very_high': {
            50: ['Monitor air quality closely and adjust workout intensity'],
            100: ['Significantly reduce outdoor training intensity or move indoors'],
            150: ['Cancel outdoor training sessions and use indoor facilities only']
        }
    }
    
    if activity_level in activity_recommendations:
        for threshold in sorted(activity_recommendations[activity_level].keys()):
            if aqi >= threshold:
                recommendations['activity_specific'].extend(activity_recommendations[activity_level][threshold])
    
    # Allergy-specific recommendations
    if allergies:
        recommendations['condition_specific'].extend([
            'Air pollution can worsen allergy symptoms',
            'Consider taking antihistamines as recommended by your doctor',
            'Use HEPA air purifiers to reduce indoor allergens'
        ])
    
    # Remove duplicates and empty lists
    for key in list(recommendations.keys()):
        if isinstance(recommendations[key], list):
            recommendations[key] = list(set(recommendations[key]))
            if not recommendations[key]:
                del recommendations[key]
    
    return recommendations if any(recommendations.values()) else None

def generate_personalized_health_advice(user_prompt, user_profile):
    """Generate personalized health advice for general health questions."""
    if not user_profile or not llm:
        return None
    
    age = user_profile.get('age', 'unspecified')
    medical_conditions = user_profile.get('medical_conditions', [])
    allergies = user_profile.get('allergies', [])
    activity_level = user_profile.get('activity_level', 'moderate')
    
    # Create personalized context
    profile_context = f"""
    User Profile:
    - Age: {age}
    - Medical conditions: {', '.join(medical_conditions) if medical_conditions else 'None reported'}
    - Allergies: {', '.join(allergies) if allergies else 'None reported'}
    - Activity level: {activity_level}
    """
    
    # Generate personalized advice using AI
    personalized_prompt = f"""
    Based on the following user profile, provide specific, personalized health advice for this question: "{user_prompt}"
    
    {profile_context}
    
    Please provide:
    1. Specific advice tailored to their age group
    2. Considerations for their medical conditions (if any)
    3. Allergy-related precautions (if applicable)
    4. Activity modifications based on their fitness level
    
    Keep the advice practical, actionable, and focused on their specific health profile.
    Format as a clear, concise response under 200 words.
    """
    
    try:
        response = llm.generate_content(personalized_prompt)
        advice_text = response.text.strip()
        
        return {
            "title": "Personalized Health Advice",
            "advice": advice_text,
            "profile_based": True,
            "considerations": {
                "age_group": get_age_group_advice(age),
                "medical_conditions": medical_conditions,
                "allergies": allergies,
                "activity_level": activity_level
            }
        }
    except Exception as e:
        print(f"Error generating personalized advice: {e}")
        return None

def get_age_group_advice(age):
    """Get age-specific health considerations."""
    try:
        age_num = int(age) if age != 'unspecified' else 30
        if age_num < 13:
            return "Children need extra protection from air pollution and environmental factors"
        elif age_num < 20:
            return "Teenagers should be aware of how air quality affects athletic performance"
        elif age_num < 65:
            return "Adults should monitor air quality for work and exercise planning"
        else:
            return "Seniors should take extra precautions with air quality and health monitoring"
    except (ValueError, TypeError):
        return "Age-appropriate health monitoring recommended"

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
    
    elif query_type == 'ozone_questions':
        return {
            "type": "pollutant_info",
            "title": "Understanding Ozone (O₃)",
            "content": {
                "what_is_it": "Ground-level ozone is a harmful air pollutant formed when nitrogen oxides and volatile organic compounds react in sunlight.",
                "health_effects": {
                    "short_term": ["Throat irritation", "Coughing", "Chest pain", "Shortness of breath", "Worsening of asthma"],
                    "long_term": ["Reduced lung function", "Increased risk of respiratory infections", "Premature aging of lungs"]
                },
                "safe_levels": {
                    "good": "0-54 ppb (AQI 0-50) - Safe for everyone",
                    "moderate": "55-70 ppb (AQI 51-100) - Acceptable for most people",
                    "unhealthy_sensitive": "71-85 ppb (AQI 101-150) - Sensitive groups should limit outdoor activities",
                    "unhealthy": "86-105 ppb (AQI 151-200) - Everyone should limit outdoor activities"
                },
                "protection_tips": [
                    "Avoid outdoor exercise during peak ozone hours (10 AM - 6 PM)",
                    "Stay indoors when ozone alerts are issued",
                    "Choose early morning or evening for outdoor activities",
                    "Use air conditioning instead of opening windows on high ozone days"
                ],
                "who_at_risk": ["Children", "Adults over 65", "People with asthma or lung disease", "Outdoor workers", "Athletes"]
            }
        }
    
    elif query_type == 'particulate_questions':
        return {
            "type": "pollutant_info",
            "title": "Particulate Matter (PM2.5 & PM10)",
            "content": {
                "what_is_it": "Particulate matter consists of tiny particles suspended in air. PM2.5 particles are 2.5 micrometers or smaller, PM10 are 10 micrometers or smaller.",
                "size_comparison": "PM2.5 is 30 times smaller than the width of a human hair and can penetrate deep into lungs and bloodstream.",
                "health_effects": {
                    "pm25": ["Heart attacks", "Irregular heartbeat", "Decreased lung function", "Increased respiratory symptoms", "Premature death"],
                    "pm10": ["Coughing", "Difficulty breathing", "Irritated eyes/nose/throat", "Aggravated asthma"]
                },
                "safe_levels": {
                    "pm25_daily": "0-12 μg/m³ (AQI 0-50) - Good",
                    "pm25_unhealthy": "35.5+ μg/m³ (AQI 151+) - Unhealthy for everyone",
                    "pm10_daily": "0-54 μg/m³ (AQI 0-50) - Good",
                    "pm10_unhealthy": "155+ μg/m³ (AQI 151+) - Unhealthy for everyone"
                },
                "sources": ["Vehicle exhaust", "Power plants", "Industrial processes", "Wildfires", "Dust storms", "Construction"],
                "protection": [
                    "Use N95 or P100 masks when PM levels are high",
                    "Run air purifiers with HEPA filters indoors",
                    "Avoid outdoor exercise when PM levels exceed 35 μg/m³",
                    "Keep windows closed during pollution episodes"
                ]
            }
        }
    
    elif query_type == 'allergy_pollen_advice':
        return {
            "type": "allergy_advice", 
            "title": "Managing Allergies and Air Quality",
            "content": {
                "air_pollution_connection": "Air pollution can worsen allergy symptoms by irritating already inflamed airways and making you more sensitive to allergens.",
                "double_trouble": "Poor air quality + high pollen = increased allergy symptoms",
                "management_strategies": {
                    "indoor": [
                        "Use HEPA air purifiers to remove both pollutants and allergens",
                        "Keep windows closed during high pollution and high pollen days",
                        "Change HVAC filters regularly",
                        "Remove shoes and wash hands when coming indoors",
                        "Shower before bed to remove pollen and pollutants"
                    ],
                    "outdoor": [
                        "Check both AQI and pollen counts before going outside",
                        "Wear wraparound sunglasses to protect eyes",
                        "Consider N95 masks on high pollution days",
                        "Avoid outdoor activities when both pollution and pollen are high",
                        "Choose early morning (6-10 AM) for outdoor activities when possible"
                    ],
                    "medication": [
                        "Take antihistamines as directed by your doctor",
                        "Use nasal saline rinses to clear pollutants and allergens",
                        "Keep rescue inhalers accessible if you have asthma",
                        "Consider starting allergy medications before peak season"
                    ]
                },
                "when_to_seek_help": [
                    "Allergy symptoms worsen during high pollution days",
                    "Difficulty breathing or wheezing",
                    "Symptoms don't improve with usual treatments",
                    "Development of new respiratory symptoms"
                ],
                "pollen_types": {
                    "spring": "Tree pollen (March-May)",
                    "summer": "Grass pollen (May-July)", 
                    "fall": "Weed pollen, especially ragweed (August-October)"
                }
            }
        }
    
    elif query_type == 'indoor_air_advice':
        return {
            "type": "indoor_air_advice",
            "title": "Improving Indoor Air Quality",
            "content": {
                "why_it_matters": "Americans spend 90% of their time indoors, where air can be 2-5 times more polluted than outdoor air.",
                "common_indoor_pollutants": [
                    "Dust mites and pet dander",
                    "Mold and mildew", 
                    "Volatile organic compounds (VOCs) from cleaning products",
                    "Cooking fumes and smoke",
                    "Formaldehyde from furniture and carpets",
                    "Radon gas (in some areas)"
                ],
                "improvement_strategies": {
                    "ventilation": [
                        "Open windows when outdoor air quality is good (AQI < 100)",
                        "Use exhaust fans in bathrooms and kitchens",
                        "Ensure HVAC system is properly maintained",
                        "Consider heat recovery ventilators (HRV) or energy recovery ventilators (ERV)"
                    ],
                    "air_purification": [
                        "Use HEPA air purifiers in main living areas",
                        "Choose purifiers rated for your room size",
                        "Replace filters regularly (every 3-6 months)",
                        "Consider UV-C light purifiers for biological contaminants"
                    ],
                    "source_control": [
                        "Use low-VOC or VOC-free products",
                        "Store chemicals in sealed containers away from living areas",
                        "Fix water leaks promptly to prevent mold",
                        "Vacuum regularly with HEPA filter",
                        "Maintain humidity between 30-50%"
                    ]
                },
                "plants_that_help": [
                    "Snake plant (removes formaldehyde)",
                    "Spider plant (removes carbon monoxide)",
                    "Peace lily (removes ammonia)",
                    "Rubber plant (removes formaldehyde)",
                    "Aloe vera (removes formaldehyde and benzene)"
                ],
                "when_outdoor_air_is_bad": [
                    "Keep windows and doors closed",
                    "Set HVAC to recirculate mode",
                    "Run air purifiers continuously",
                    "Avoid activities that create indoor pollution (cooking, cleaning, smoking)"
                ]
            }
        }
    
    elif query_type == 'wildfire_smoke_advice':
        return {
            "type": "wildfire_advice",
            "title": "Protecting Yourself from Wildfire Smoke",
            "content": {
                "what_is_wildfire_smoke": "A complex mixture of gases and particles from burning vegetation, containing PM2.5, carbon monoxide, formaldehyde, and other harmful compounds.",
                "health_effects": {
                    "immediate": ["Eye and throat irritation", "Coughing", "Runny nose", "Headaches", "Difficulty breathing"],
                    "serious": ["Chest pain", "Fast heartbeat", "Wheezing", "Severe cough", "Shortness of breath"]
                },
                "most_at_risk": [
                    "People with heart or lung conditions",
                    "Children under 18",
                    "Adults over 65", 
                    "Pregnant women",
                    "Outdoor workers",
                    "People experiencing homelessness"
                ],
                "protection_strategies": {
                    "stay_indoors": [
                        "Keep windows and doors closed",
                        "Run air conditioning on recirculate mode",
                        "Use portable air cleaners with HEPA filters",
                        "Avoid activities that create more particles (smoking, candles, frying)"
                    ],
                    "if_you_must_go_outside": [
                        "Wear N95 or P100 respirator masks",
                        "Limit outdoor activities and time spent outside",
                        "Avoid vigorous outdoor exercise",
                        "Seek indoor shelter as soon as possible"
                    ],
                    "diy_air_cleaner": [
                        "Create a box fan filter using MERV 13 filters",
                        "Tape filters to intake side of fan",
                        "Run on medium speed in main living area",
                        "Can reduce PM2.5 by 50-90% in a room"
                    ]
                },
                "when_to_seek_medical_care": [
                    "Difficulty breathing or shortness of breath",
                    "Chest pain or heart palpitations", 
                    "Severe cough or wheezing",
                    "Symptoms worsen despite staying indoors"
                ],
                "evacuation_considerations": [
                    "If visibility is less than 5 miles due to smoke",
                    "If you have respiratory conditions and symptoms worsen",
                    "If you don't have air conditioning or air cleaners",
                    "Consider staying with friends/family in cleaner air areas"
                ]
            }
        }
        
    elif query_type == 'protection_advice':
        return {
            "type": "protection_advice",
            "title": "Personal Protection from Air Pollution",
            "content": {
                "mask_guidance": {
                    "when_to_wear": "When AQI > 150, during wildfires, or if you're sensitive and AQI > 100",
                    "n95_masks": {
                        "effectiveness": "Filters 95% of particles ≥ 0.3 micrometers",
                        "best_for": "PM2.5, dust, pollen, wildfire smoke",
                        "fit_tips": ["Check for gaps around edges", "Pinch nose bridge", "Should feel resistance when breathing"]
                    },
                    "surgical_masks": {
                        "effectiveness": "Limited protection against fine particles",
                        "best_for": "Large droplets, some dust",
                        "note": "Not recommended for air pollution protection"
                    },
                    "p100_masks": {
                        "effectiveness": "Filters 99.97% of particles",
                        "best_for": "Severe pollution events, industrial areas",
                        "note": "More protective but harder to breathe through"
                    }
                },
                "indoor_protection": [
                    "Create a 'clean room' with air purifier",
                    "Seal gaps around windows and doors",
                    "Use high-efficiency furnace filters (MERV 13+)",
                    "Run bathroom and kitchen exhaust fans",
                    "Avoid indoor pollution sources"
                ],
                "outdoor_strategies": [
                    "Time outdoor activities for cleaner air periods",
                    "Choose routes away from busy roads",
                    "Exercise in parks rather than urban areas",
                    "Monitor real-time air quality before going out"
                ],
                "for_sensitive_groups": {
                    "children": ["Limit outdoor time when AQI > 100", "Watch for symptoms during play", "Keep rescue medications handy"],
                    "elderly": ["Stay indoors during poor air quality", "Have emergency plan", "Monitor health closely"],
                    "lung_conditions": ["Follow action plans", "Have medications accessible", "Consider air quality in daily planning"],
                    "heart_conditions": ["Avoid outdoor exercise when AQI > 100", "Monitor for chest pain/fatigue", "Consult doctor about air quality concerns"]
                }
            }
        }
    
    elif query_type == 'exercise_advice':
        return {
            "type": "exercise_advice",
            "title": "Exercising Safely During Poor Air Quality",
            "content": {
                "why_exercise_matters": "Exercise increases breathing rate, causing you to inhale more polluted air deeper into your lungs.",
                "general_guidelines": {
                    "good_air": "AQI 0-50: Safe for all outdoor activities",
                    "moderate_air": "AQI 51-100: Sensitive people should consider reducing prolonged outdoor exertion",
                    "unhealthy_sensitive": "AQI 101-150: Sensitive groups should move activities indoors",
                    "unhealthy": "AQI 151-200: Everyone should move activities indoors",
                    "very_unhealthy": "AQI 201+: Avoid all outdoor activities"
                },
                "indoor_alternatives": [
                    "Home workout videos or apps",
                    "Gym with good air filtration",
                    "Mall walking programs",
                    "Indoor swimming pools",
                    "Yoga or stretching routines",
                    "Stair climbing in clean buildings"
                ],
                "timing_strategies": {
                    "best_times": ["Early morning (6-10 AM)", "Late evening after sunset"],
                    "avoid": ["Rush hour traffic times", "Peak sun hours (10 AM - 4 PM)", "During temperature inversions"],
                    "check_forecasts": "Air quality often changes throughout the day"
                },
                "location_choices": [
                    "Parks away from busy roads",
                    "Waterfront areas with better air circulation", 
                    "Higher elevations when possible",
                    "Areas upwind from pollution sources",
                    "Avoid: busy streets, industrial areas, construction zones"
                ],
                "warning_signs_to_stop": [
                    "Unusual coughing or throat irritation",
                    "Chest tightness or pain",
                    "Unusual fatigue or shortness of breath",
                    "Headache or dizziness",
                    "Eye or nose irritation"
                ],
                "special_considerations": {
                    "athletes": ["Train indoors during poor air quality", "Monitor performance changes", "Stay extra hydrated"],
                    "beginners": ["Start with indoor activities", "Build fitness before outdoor pollution exposure"],
                    "children_sports": ["Cancel outdoor practices when AQI > 150", "Watch for symptoms in young athletes"]
                }
            }
        }
    
    elif query_type == 'nitrogen_questions':
        return {
            "type": "pollutant_info",
            "title": "Nitrogen Dioxide (NO₂) Information",
            "content": {
                "what_is_it": "A reddish-brown gas primarily from vehicle exhaust and power plants that contributes to smog formation.",
                "health_effects": ["Respiratory irritation", "Increased susceptibility to infections", "Worsening of asthma", "Reduced lung function"],
                "main_sources": ["Vehicle exhaust", "Power plants", "Industrial facilities", "Gas appliances"],
                "safe_levels": "EPA standard: 100 ppb (1-hour average), 53 ppb (annual average)",
                "protection": ["Avoid busy roads during rush hour", "Use exhaust fans with gas appliances", "Support clean transportation policies"]
            }
        }
    
    elif query_type == 'sulfur_questions':
        return {
            "type": "pollutant_info", 
            "title": "Sulfur Dioxide (SO₂) Information",
            "content": {
                "what_is_it": "A colorless gas with a sharp odor, primarily from fossil fuel combustion at power plants and industrial facilities.",
                "health_effects": ["Respiratory irritation", "Breathing difficulties", "Worsening of asthma", "Eye irritation"],
                "main_sources": ["Coal-fired power plants", "Oil refineries", "Metal processing", "Volcanic eruptions"],
                "safe_levels": "EPA standard: 75 ppb (1-hour average)",
                "protection": ["Stay indoors during high SO₂ episodes", "Use air purifiers", "Support clean energy initiatives"]
            }
        }
    
    elif query_type == 'carbon_monoxide_questions':
        return {
            "type": "pollutant_info",
            "title": "Carbon Monoxide (CO) Information", 
            "content": {
                "what_is_it": "A colorless, odorless gas produced by incomplete combustion of carbon-containing fuels.",
                "health_effects": ["Headaches", "Dizziness", "Weakness", "Nausea", "Confusion", "At high levels: death"],
                "main_sources": ["Vehicle exhaust", "Faulty heating systems", "Gas appliances", "Generators", "Charcoal grills"],
                "safe_levels": "EPA standard: 9 ppm (8-hour average), 35 ppm (1-hour average)",
                "protection": ["Install CO detectors", "Never use generators indoors", "Maintain heating systems", "Don't idle vehicles in garages"],
                "emergency_signs": ["Severe headache", "Dizziness", "Confusion", "Nausea - seek immediate medical attention"]
            }
        }
    
    elif query_type == 'general_advice' or query_type == 'protection_advice':
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
    
    elif query_type == 'general_air_quality':
        # Use LLM to generate a relevant response for general air quality questions
        try:
            ai_prompt = f"""You are an expert air quality specialist with deep knowledge of air pollution, health effects, and environmental science. Answer this question comprehensively and accurately: "{user_prompt}"

            Guidelines for your response:
            1. Provide specific, actionable information with numbers/thresholds when relevant
            2. Include health implications and who might be most at risk
            3. Mention relevant AQI levels, pollutant concentrations, or safety standards
            4. Offer practical advice for protection or improvement
            5. Keep the tone professional but accessible
            6. Structure your response clearly with bullet points or sections when appropriate
            7. If the question is about "good" or "safe" levels, provide specific numerical ranges and AQI categories
            
            Focus on being helpful and informative about air quality topics including AQI, pollutants, health effects, protection strategies, and environmental conditions."""
            
            ai_response = llm.generate_content(ai_prompt)
            
            return {
                "type": "ai_generated",
                "title": "Air Quality Information",
                "content": {
                    "ai_response": ai_response.text,
                    "note": "This response was generated using AI based on current air quality knowledge and research."
                }
            }
        except Exception as e:
            print(f"Error generating AI response: {e}")
            return {
                "type": "general_advice",
                "title": "General Air Quality Information", 
                "content": {
                    "message": "I can help with air quality questions! Try asking about specific pollutants (ozone, PM2.5), health effects, protection strategies, or indoor air quality.",
                    "examples": [
                        "What is a good AQI range?",
                        "How much ozone is too much?",
                        "What are safe PM2.5 levels?",
                        "When should I be concerned about air quality?",
                        "What's the difference between PM2.5 and PM10?",
                        "How can I improve my indoor air quality?"
                    ]
                }
            }
    
    elif query_type == 'out_of_domain':
        return {
            "type": "out_of_domain",
            "title": "Outside My Air Quality Expertise",
            "content": {
                "message": f"I'm sorry, but your question about '{user_prompt}' appears to be outside my area of expertise. I'm specifically designed to help with air quality and environmental health topics.",
                "what_i_can_help_with": [
                    "Air Quality Index (AQI) explanations and safe ranges",
                    "Specific pollutants (PM2.5, ozone, NO₂, SO₂, CO)",
                    "Health effects of air pollution on different groups",
                    "Personal protection strategies and mask recommendations",
                    "Indoor air quality improvement techniques",
                    "Wildfire smoke safety and protection",
                    "Allergy management during poor air quality",
                    "Exercise and outdoor activity guidelines",
                    "Air purifier recommendations and effectiveness",
                    "Understanding air quality monitoring and data"
                ],
                "redirect": "I'd be happy to help you with any air quality, pollution, or environmental health questions instead!",
                "examples": [
                    "What is a good AQI range for outdoor activities?",
                    "How much PM2.5 is considered safe?",
                    "Should I wear a mask when AQI is over 100?",
                    "How can I protect myself from wildfire smoke?",
                    "What's the best air purifier for allergies?",
                    "Is it safe to exercise when ozone levels are high?"
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
        if query_type in ['aqi_explanation', 'health_advice', 'general_advice', 'ozone_questions', 
                         'particulate_questions', 'nitrogen_questions', 'sulfur_questions', 
                         'carbon_monoxide_questions', 'allergy_pollen_advice', 'indoor_air_advice', 
                         'wildfire_smoke_advice', 'protection_advice', 'exercise_advice', 
                         'general_air_quality', 'out_of_domain']:
            general_response = handle_general_questions(query_type, user_prompt)
            if general_response:
                # Add personalized recommendations for general health questions
                user = get_user_from_token()
                if user:
                    user_id = user['user_id']
                    user_profile = user_profiles.get(user_id)
                    
                    if user_profile and any([user_profile.get('age'), user_profile.get('medical_conditions'), 
                                           user_profile.get('allergies'), user_profile.get('activity_level')]):
                        # Generate personalized advice for general health questions
                        personalized_advice = generate_personalized_health_advice(user_prompt, user_profile)
                        general_response["personalized_recommendations"] = personalized_advice
                    else:
                        general_response["personalized_recommendations"] = {
                            "message": "Create your health profile to receive personalized advice for your specific health conditions.",
                            "call_to_action": "Click 'Health Profile' to get personalized recommendations!"
                        }
                else:
                    general_response["personalized_recommendations"] = {
                        "message": "Log in to receive health advice tailored to your personal medical conditions and lifestyle.",
                        "call_to_action": "Click 'Login' to access personalized features!"
                    }
                
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

        # Always check for user authentication and add personalized recommendations
        user = get_user_from_token()
        if user:
            user_id = user['user_id']
            user_profile = user_profiles.get(user_id)
            
            # Generate personalized recommendations based on user profile
            if user_profile and any([user_profile.get('age'), user_profile.get('medical_conditions'), 
                                   user_profile.get('allergies'), user_profile.get('activity_level')]):
                personalized_rec = generate_personalized_recommendations(aqi_data, user_profile)
                explanation_json["personalized_recommendations"] = personalized_rec
            else:
                # User is authenticated but has no profile - encourage them to create one
                explanation_json["personalized_recommendations"] = {
                    "message": "Create your health profile to receive personalized air quality recommendations based on your age, medical conditions, and activity level.",
                    "call_to_action": "Click 'Health Profile' to get started with personalized advice!"
                }
        else:
            # User is not authenticated - encourage login for personalized features
            explanation_json["personalized_recommendations"] = {
                "message": "Log in to receive personalized air quality recommendations tailored to your health profile.",
                "call_to_action": "Click 'Login' to access personalized health advice!"
            }

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