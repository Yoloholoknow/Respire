import os
from flask import Flask, request, jsonify
import google.generativeai as genai
import requests
from dotenv import load_dotenv
import json
import traceback
import time
from functools import lru_cache

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

# Check API configuration on startup
print(f"WAQI API Token configured: {'Yes' if WAQI_API_TOKEN else 'No'}")
print(f"Google Maps API Key configured: {'Yes' if MAPS_API_KEY else 'No'}")
if WAQI_API_TOKEN:
    print(f"WAQI Token preview: {WAQI_API_TOKEN[:8]}..." if len(WAQI_API_TOKEN) > 8 else "Token too short")

# --- Flask App Initialization ---
app = Flask(__name__)

# Simple cache for WAQI responses (5 minute TTL)
waqi_cache = {}
CACHE_TTL = 300  # 5 minutes

# Heatmap cache for expensive operations (10 minute TTL)
heatmap_cache = {}
HEATMAP_CACHE_TTL = 600  # 10 minutes

# Rate limiting for WAQI requests
waqi_request_times = []
MAX_WAQI_REQUESTS_PER_MINUTE = 50  # Increased limit for better performance

# Circuit breaker for WAQI failures
waqi_failure_count = 0
waqi_circuit_breaker_time = 0
WAQI_FAILURE_THRESHOLD = 5
WAQI_CIRCUIT_BREAKER_DURATION = 300  # 5 minutes

def get_cached_waqi_data(lat, lng):
    """Get cached WAQI data if available and not expired."""
    cache_key = f"{lat:.4f},{lng:.4f}"
    if cache_key in waqi_cache:
        data, timestamp = waqi_cache[cache_key]
        if time.time() - timestamp < CACHE_TTL:
            return data
        else:
            # Remove expired entry
            del waqi_cache[cache_key]
    return None

def cache_waqi_data(lat, lng, data):
    """Cache WAQI data with timestamp."""
    cache_key = f"{lat:.4f},{lng:.4f}"
    waqi_cache[cache_key] = (data, time.time())

def can_make_waqi_request():
    """Check if we can make a WAQI request without hitting rate limits."""
    current_time = time.time()
    # Remove requests older than 1 minute
    waqi_request_times[:] = [t for t in waqi_request_times if current_time - t < 60]
    return len(waqi_request_times) < MAX_WAQI_REQUESTS_PER_MINUTE

def record_waqi_request():
    """Record that we made a WAQI request."""
    waqi_request_times.append(time.time())

def get_cached_heatmap_data(bounds_key):
    """Get cached heatmap data if available and not expired."""
    if bounds_key in heatmap_cache:
        data, timestamp = heatmap_cache[bounds_key]
        if time.time() - timestamp < HEATMAP_CACHE_TTL:
            return data
        else:
            # Remove expired entry
            del heatmap_cache[bounds_key]
    return None

def cache_heatmap_data(bounds_key, data):
    """Cache heatmap data with timestamp."""
    heatmap_cache[bounds_key] = (data, time.time())

def is_waqi_circuit_breaker_open():
    """Check if WAQI circuit breaker is open due to too many failures."""
    global waqi_circuit_breaker_time
    if waqi_failure_count >= WAQI_FAILURE_THRESHOLD:
        if time.time() - waqi_circuit_breaker_time < WAQI_CIRCUIT_BREAKER_DURATION:
            return True
        else:
            # Reset circuit breaker after duration
            reset_waqi_circuit_breaker()
    return False

def record_waqi_failure():
    """Record a WAQI API failure."""
    global waqi_failure_count, waqi_circuit_breaker_time
    waqi_failure_count += 1
    waqi_circuit_breaker_time = time.time()
    print(f"WAQI failure recorded. Count: {waqi_failure_count}")

def record_waqi_success():
    """Record a successful WAQI API call."""
    global waqi_failure_count
    waqi_failure_count = max(0, waqi_failure_count - 1)  # Gradually reduce failure count

def reset_waqi_circuit_breaker():
    """Reset the WAQI circuit breaker."""
    global waqi_failure_count, waqi_circuit_breaker_time
    waqi_failure_count = 0
    waqi_circuit_breaker_time = 0
    print("WAQI circuit breaker reset")

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
        # Check cache first
        cached_data = get_cached_waqi_data(lat, lng)
        if cached_data:
            print(f"Using cached WAQI data for {lat}, {lng}")
            return cached_data
        
        # Check circuit breaker first
        if is_waqi_circuit_breaker_open():
            print(f"WAQI circuit breaker is open, skipping API call for {lat}, {lng}")
        # Check rate limit
        elif not can_make_waqi_request():
            print(f"WAQI rate limit reached, falling back to estimation for {lat}, {lng}")
        else:
            try:
                url = f"https://api.waqi.info/feed/geo:{lat};{lng}/?token={WAQI_API_TOKEN}"
                print(f"Querying WAQI for {lat}, {lng}")
                record_waqi_request()
                resp = requests.get(url, timeout=1.5)
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
                    # Cache the result
                    cache_waqi_data(lat, lng, unified)
                    record_waqi_success()
                    return unified
                else:
                    print(f"WAQI returned no data for {lat},{lng}: {data.get('status')}")
                    record_waqi_failure()
            except requests.exceptions.RequestException as e:
                print(f"WAQI request error: {e}")
                record_waqi_failure()    # If WAQI not configured or failed, fall back to estimation with synthetic pollutant data
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

@app.route('/api/cache-status', methods=['GET'])
def cache_status():
    """Get cache status and performance info."""
    return jsonify({
        "waqi_cache_size": len(waqi_cache),
        "heatmap_cache_size": len(heatmap_cache),
        "recent_waqi_requests": len([t for t in waqi_request_times if time.time() - t < 60]),
        "cache_ttl_seconds": CACHE_TTL,
        "heatmap_cache_ttl_seconds": HEATMAP_CACHE_TTL,
        "max_requests_per_minute": MAX_WAQI_REQUESTS_PER_MINUTE,
        "waqi_failure_count": waqi_failure_count,
        "waqi_circuit_breaker_open": is_waqi_circuit_breaker_open(),
        "waqi_circuit_breaker_time_remaining": max(0, WAQI_CIRCUIT_BREAKER_DURATION - (time.time() - waqi_circuit_breaker_time)) if waqi_failure_count >= WAQI_FAILURE_THRESHOLD else 0
    })

@app.route('/api/clear-cache', methods=['POST'])
def clear_cache():
    """Clear all caches for testing purposes."""
    global waqi_cache, waqi_request_times, heatmap_cache
    waqi_cache.clear()
    waqi_request_times.clear()
    heatmap_cache.clear()
    reset_waqi_circuit_breaker()
    # Clear LRU caches
    estimate_pollution_by_location.cache_clear()
    is_ocean_area.cache_clear()
    return jsonify({"message": "All caches cleared, circuit breaker reset"})

@app.route('/api/fast-heatmap', methods=['POST'])
def fast_heatmap():
    """Ultra-fast heatmap generation for quick zoom/pan operations."""
    try:
        body = request.get_json(silent=True) or {}
    except Exception:
        body = {}

    sw = body.get('sw')
    ne = body.get('ne')
    max_points = int(body.get('max_points', 400))  # Reduced default for speed

    if not sw or not ne:
        return jsonify({"error": "Bounds required for fast heatmap"}), 400

    # Create smaller cache key
    bounds_key = f"fast_{sw.get('lat'):.1f},{sw.get('lng'):.1f}_{ne.get('lat'):.1f},{ne.get('lng'):.1f}"
    
    # Check cache first
    cached_data = get_cached_heatmap_data(bounds_key)
    if cached_data:
        return jsonify(cached_data)

    heatmap_points = []
    
    try:
        lat_min = float(sw.get('lat'))
        lng_min = float(sw.get('lng'))
        lat_max = float(ne.get('lat'))
        lng_max = float(ne.get('lng'))

        if lat_min > lat_max:
            lat_min, lat_max = lat_max, lat_min

        # Fast grid generation - larger steps for speed
        area_size = (lat_max - lat_min) * (lng_max - lng_min)
        if area_size > 1000:
            target_cells = 10.0
        elif area_size > 100:
            target_cells = 15.0
        else:
            target_cells = 20.0
            
        lat_step = max(1.0, (lat_max - lat_min) / target_cells)
        lng_step = max(1.0, (lng_max - lng_min) / target_cells)

        lat = lat_min
        while lat <= lat_max:
            lng = lng_min
            while lng <= lng_max:
                estimated_aqi = estimate_pollution_by_location(lat, lng)
                heatmap_points.append({
                    "lat": lat,
                    "lng": lng,
                    "aqi": estimated_aqi,
                    "weight": estimated_aqi,
                    "estimated": True
                })
                lng += lng_step
            lat += lat_step

        # Limit points
        if len(heatmap_points) > max_points:
            step = len(heatmap_points) // max_points
            heatmap_points = heatmap_points[::step]

        # Cache result
        cache_heatmap_data(bounds_key, heatmap_points)
        
        return jsonify(heatmap_points)
        
    except Exception as e:
        print(f"Error in fast heatmap: {e}")
        return jsonify({"error": "Fast heatmap generation failed"}), 500

@app.route('/api/quick-aqi', methods=['POST'])
def quick_aqi():
    """Fast AQI estimation without full data processing."""
    data = request.get_json()
    lat = data.get('lat')
    lng = data.get('lng')
    
    if not lat or not lng:
        return jsonify({"error": "Latitude and longitude are required"}), 400
    
    # Quick estimation using cached function
    estimated_aqi = estimate_pollution_by_location(float(lat), float(lng))
    
    # Simple category determination
    if estimated_aqi <= 50:
        category = "Good"
        color = "#00E400"
    elif estimated_aqi <= 100:
        category = "Moderate" 
        color = "#FFFF00"
    elif estimated_aqi <= 150:
        category = "Unhealthy for Sensitive Groups"
        color = "#FF7E00"
    elif estimated_aqi <= 200:
        category = "Unhealthy"
        color = "#FF0000"
    elif estimated_aqi <= 300:
        category = "Very Unhealthy"
        color = "#8F3F97"
    else:
        category = "Hazardous"
        color = "#7E0023"
    
    return jsonify({
        "aqi": estimated_aqi,
        "category": category,
        "color": color,
        "provider": "estimate_fast"
    })

@app.route('/api/test-waqi', methods=['GET'])
def test_waqi():
    """Test WAQI API connectivity."""
    if not WAQI_API_TOKEN:
        return jsonify({
            "status": "error",
            "message": "WAQI API token not configured",
            "configured": False
        }), 400
    
    try:
        # Test with a known location (Beijing)
        test_url = f"https://api.waqi.info/feed/geo:39.9042;116.4074/?token={WAQI_API_TOKEN}"
        resp = requests.get(test_url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("status") == "ok":
            return jsonify({
                "status": "success",
                "message": "WAQI API is working",
                "configured": True,
                "test_data": {
                    "aqi": data.get("data", {}).get("aqi"),
                    "city": data.get("data", {}).get("city", {}).get("name"),
                    "pollutants": list(data.get("data", {}).get("iaqi", {}).keys())
                }
            })
        else:
            return jsonify({
                "status": "error",
                "message": f"WAQI API returned status: {data.get('status')}",
                "configured": True,
                "waqi_response": data
            }), 400
            
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"WAQI API test failed: {str(e)}",
            "configured": True
        }), 500

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
    max_points = int(body.get('max_points', 800))

    # Create cache key from bounds
    bounds_key = "global"
    if sw and ne:
        bounds_key = f"{sw.get('lat'):.2f},{sw.get('lng'):.2f}_{ne.get('lat'):.2f},{ne.get('lng'):.2f}_{max_points}"
    
    # Check cache first
    cached_data = get_cached_heatmap_data(bounds_key)
    if cached_data:
        print(f"Using cached heatmap data for bounds: {bounds_key}")
        return jsonify(cached_data)

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

            # Adaptive grid density based on zoom level
            area_size = (lat_max - lat_min) * lng_span
            if area_size > 1000:  # Very zoomed out
                target_cells = 15.0
            elif area_size > 100:  # Medium zoom
                target_cells = 20.0
            else:  # Zoomed in
                target_cells = 25.0
                
            lat_step = max(0.5, min(8.0, (lat_max - lat_min) / target_cells if (lat_max - lat_min) > 0 else 1.0))
            lng_step = max(0.5, min(8.0, lng_span / target_cells if lng_span > 0 else 1.0))

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

        # Skip WAQI station fetching for faster heatmap generation
        # This prevents timeout cascade failures that slow down the interface
        print("Skipping WAQI station fetch for faster heatmap performance")
            
        # Skip real data fetching for heatmap to prevent timeout delays
        # Use only cached real data if available
        try:
            cached_real_points = []
            test_locations = [
                (40.7128, -74.0060, "NYC"),
                (34.0522, -118.2437, "LA"),
                (39.9042, 116.4074, "Beijing")
            ]
            
            for lat_test, lng_test, name in test_locations:
                if lat_min <= lat_test <= lat_max and lng_min <= lng_test <= lng_max:
                    cached_data = get_cached_waqi_data(lat_test, lng_test)
                    if cached_data and cached_data.get('aqi'):
                        cached_real_points.append({
                            "lat": lat_test,
                            "lng": lng_test,
                            "aqi": cached_data.get('aqi'),
                            "weight": cached_data.get('aqi'),
                            "estimated": False,
                            "station_name": name
                        })
            
            heatmap_points.extend(cached_real_points)
            print(f"Added {len(cached_real_points)} cached real data points")
        except Exception as e:
            print(f"Error adding cached real data: {e}")

    else:
        # No bounds provided: generate a coarse global grid but cap size
        lat_step = 10
        lng_step = 12
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
    
    # Cache the result
    cache_heatmap_data(bounds_key, heatmap_points)
    
    return jsonify(heatmap_points)

@lru_cache(maxsize=1000)
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
    {"center": (24.7136, 46.6753), "radius": 8, "pollution": 85},    # Riyadh
    {"center": (30.0444, 31.2357), "radius": 8, "pollution": 80},    # Cairo

    # Europe industrial
    {"center": (51.1657, 10.4515), "radius": 12, "pollution": 70},   # Germany central
    {"center": (48.8566, 2.3522), "radius": 12, "pollution": 65},    # Paris
    {"center": (51.5074, -0.1278), "radius": 12, "pollution": 60},   # London
    {"center": (52.3791, 4.9009), "radius": 12, "pollution": 55},    # Amsterdam
    {"center": (41.9028, 12.4964), "radius": 12, "pollution": 50},   # Rome

    # North America - Enhanced coverage
    {"center": (34.0522, -118.2437), "radius": 10, "pollution": 85},  # Los Angeles
    {"center": (40.7128, -74.0060), "radius": 10, "pollution": 75},   # New York City
    {"center": (41.8781, -87.6298), "radius": 8, "pollution": 70},   # Chicago
    {"center": (29.7604, -95.3698), "radius": 8, "pollution": 65},   # Houston
    {"center": (37.7749, -122.4194), "radius": 8, "pollution": 60},  # San Francisco
    {"center": (47.6062, -122.3321), "radius": 8, "pollution": 55},  # Seattle (NW USA)
    {"center": (45.5152, -122.6784), "radius": 8, "pollution": 60},  # Portland (NW USA)
    {"center": (49.2827, -123.1207), "radius": 8, "pollution": 50},  # Vancouver (NW)
    {"center": (33.4484, -112.0740), "radius": 8, "pollution": 75},  # Phoenix
    {"center": (39.7392, -104.9903), "radius": 8, "pollution": 65},  # Denver
    {"center": (40.7589, -111.8883), "radius": 6, "pollution": 70},  # Salt Lake City

    # Mexico - Added coverage
    {"center": (19.4326, -99.1332), "radius": 12, "pollution": 120}, # Mexico City
    {"center": (25.6866, -100.3161), "radius": 8, "pollution": 90},  # Monterrey
    {"center": (20.6597, -103.3496), "radius": 8, "pollution": 85},  # Guadalajara
    {"center": (21.1619, -86.8515), "radius": 6, "pollution": 70},   # Cancun
    {"center": (32.5149, -117.0382), "radius": 8, "pollution": 80},  # Tijuana
    {"center": (31.6904, -106.4245), "radius": 6, "pollution": 75},  # Juarez

    # South America - Enhanced
    {"center": (-23.5505, -46.6333), "radius": 10, "pollution": 90}, # São Paulo
    {"center": (-22.9068, -43.1729), "radius": 8, "pollution": 80},  # Rio de Janeiro
    {"center": (-34.6118, -58.3960), "radius": 8, "pollution": 85},  # Buenos Aires
    {"center": (-33.4489, -70.6693), "radius": 8, "pollution": 95},  # Santiago, Chile
    {"center": (-23.6821, -70.4126), "radius": 6, "pollution": 85},  # Antofagasta, Chile (mining)
    {"center": (-36.8485, -73.0524), "radius": 6, "pollution": 80},  # Concepción, Chile
    {"center": (-33.0458, -71.6197), "radius": 6, "pollution": 90},  # Valparaíso, Chile
    {"center": (4.7110, -74.0721), "radius": 8, "pollution": 85},    # Bogotá
    {"center": (-12.0464, -77.0428), "radius": 8, "pollution": 90},   # Lima

    # Asia Pacific - Enhanced
    {"center": (35.6762, 139.6503), "radius": 10, "pollution": 75},  # Tokyo
    {"center": (-33.8688, 151.2093), "radius": 8, "pollution": 65},  # Sydney
    {"center": (-37.8136, 144.9631), "radius": 8, "pollution": 70},  # Melbourne
    {"center": (1.3521, 103.8198), "radius": 8, "pollution": 80},    # Singapore
    {"center": (14.5995, 120.9842), "radius": 10, "pollution": 100}, # Manila
    {"center": (-6.2088, 106.8456), "radius": 10, "pollution": 110}, # Jakarta
    {"center": (3.1390, 101.6869), "radius": 8, "pollution": 95},    # Kuala Lumpur

    # Africa and Middle East - Enhanced
    {"center": (39.9042, 32.8597), "radius": 8, "pollution": 60},   # Ankara
    {"center": (-26.2041, 28.0473), "radius": 8, "pollution": 85},  # Johannesburg
    {"center": (-33.9249, 18.4241), "radius": 6, "pollution": 70},  # Cape Town
    {"center": (6.5244, 3.3792), "radius": 8, "pollution": 100},    # Lagos
    {"center": (30.3753, 69.3451), "radius": 8, "pollution": 110},  # Pakistan industrial

    # Eastern Europe and Russia
    {"center": (55.7558, 37.6173), "radius": 10, "pollution": 65},  # Moscow
    {"center": (50.4501, 30.5234), "radius": 8, "pollution": 70},   # Kiev
    {"center": (52.2297, 21.0122), "radius": 8, "pollution": 60},   # Warsaw
    {"center": (59.9311, 30.3609), "radius": 8, "pollution": 55},   # St. Petersburg
]

    
    max_pollution = base_pollution
    
    # Add regional base pollution factors
    regional_factors = {
        # Higher base pollution for industrial regions
        'china': {'bounds': [(18.0, 53.0), (73.0, 135.0)], 'factor': 1.3},
        'india': {'bounds': [(6.0, 37.0), (68.0, 97.0)], 'factor': 1.4},
        'mexico': {'bounds': [(14.0, 33.0), (-118.0, -86.0)], 'factor': 1.2},
        'chile': {'bounds': [(-56.0, -17.0), (-109.0, -66.0)], 'factor': 1.15},
        'nw_usa': {'bounds': [(42.0, 49.0), (-125.0, -110.0)], 'factor': 1.1},
        'california': {'bounds': [(32.0, 42.0), (-125.0, -114.0)], 'factor': 1.2},
        'eastern_europe': {'bounds': [(44.0, 70.0), (12.0, 50.0)], 'factor': 1.1}
    }
    
    # Apply regional factors
    for region, info in regional_factors.items():
        (lat_min, lat_max), (lng_min, lng_max) = info['bounds']
        if lat_min <= lat <= lat_max and lng_min <= lng <= lng_max:
            base_pollution = int(base_pollution * info['factor'])
            break
    
    # Check proximity to pollution hotspots
    for hotspot in pollution_hotspots:
        center_lat, center_lng = hotspot["center"]
        distance = ((lat - center_lat) ** 2 + (lng - center_lng) ** 2) ** 0.5
        
        if distance < hotspot["radius"]:
            # Calculate pollution based on distance from center
            influence = max(0, 1 - (distance / hotspot["radius"]))
            pollution_contribution = hotspot["pollution"] * influence
            max_pollution = max(max_pollution, base_pollution + pollution_contribution)
    
    # Add altitude factor (higher altitudes often have cleaner air)
    altitude_factor = 1.0
    if abs(lat) > 45:  # Northern/Southern regions
        altitude_factor = 0.9
    elif abs(lat) < 10:  # Equatorial regions (often more humid, less dispersal)
        altitude_factor = 1.1
    
    max_pollution = int(max_pollution * altitude_factor)
    
    # Add some randomness for natural variation
    variation = (hash(f"{lat}_{lng}_var") % 20) - 10
    final_pollution = max(5, min(200, int(max_pollution + variation)))
    
    return final_pollution

@lru_cache(maxsize=500)
def is_ocean_area(lat, lng):
    """Determine if coordinates are likely over ocean - optimized for speed."""
    # Quick checks for extreme latitudes
    if lat > 70 or lat < -60:  # Arctic/Antarctic
        return True
    
    # Major ocean regions (optimized order - most common first)
    if lng < -120 or lng > 150:  # Pacific
        # Exclude major land masses
        if not (lat > 45 and lng > -130 and lng < -100):  # North America west
            if not (lat > -50 and lat < 10 and lng > 110):  # Australia/Asia
                return True
    
    if lng > -60 and lng < 20:  # Atlantic
        if lat > 50 or lat < -20:  # Exclude Europe/Africa belt
            return True
    
    if lng > 60 and lng < 120 and lat < -10:  # Indian Ocean
        return True
        
    return False

def get_waqi_stations_in_bounds(lat_min, lng_min, lat_max, lng_max, max_stations=50):
    """Get WAQI stations within specified bounds using the search API."""
    if not WAQI_API_TOKEN:
        return []
    
    stations = []
    
    # WAQI search by bounds (this is a more comprehensive approach)
    try:
        # Use WAQI's search API to find stations in the area
        # We'll sample a few points within the bounds and find nearby stations
        lat_samples = [lat_min + (lat_max - lat_min) * i / 2 for i in range(3)]
        lng_samples = [lng_min + (lng_max - lng_min) * i / 2 for i in range(3)]
        
        unique_stations = set()
        
        for lat_sample in lat_samples:
            for lng_sample in lng_samples:
                if len(unique_stations) >= max_stations:
                    break
                    
                try:
                    # Use the geo endpoint to find the nearest station
                    url = f"https://api.waqi.info/feed/geo:{lat_sample};{lng_sample}/?token={WAQI_API_TOKEN}"
                    resp = requests.get(url, timeout=1)
                    resp.raise_for_status()
                    data = resp.json()
                    
                    if data.get("status") == "ok" and isinstance(data.get("data"), dict):
                        station_data = data["data"]
                        station_lat = station_data.get("city", {}).get("geo", [None, None])[0]
                        station_lng = station_data.get("city", {}).get("geo", [None, None])[1]
                        station_aqi = station_data.get("aqi")
                        station_name = station_data.get("city", {}).get("name", "Unknown")
                        
                        if (station_lat and station_lng and station_aqi and 
                            lat_min <= station_lat <= lat_max and lng_min <= station_lng <= lng_max):
                            
                            station_key = f"{station_lat:.4f},{station_lng:.4f}"
                            if station_key not in unique_stations:
                                unique_stations.add(station_key)
                                stations.append({
                                    "lat": station_lat,
                                    "lng": station_lng,
                                    "aqi": station_aqi,
                                    "weight": station_aqi,
                                    "estimated": False,
                                    "station_name": station_name
                                })
                                
                except Exception as e:
                    print(f"Error fetching WAQI station data for {lat_sample}, {lng_sample}: {e}")
                    continue
                    
            if len(unique_stations) >= max_stations:
                break
                
    except Exception as e:
        print(f"Error in WAQI stations search: {e}")
    
    print(f"Found {len(stations)} real WAQI stations in bounds")
    return stations

def get_limited_real_data():
    """Get a small amount of real API data for key locations."""
    real_points = []
    
    # Key cities where we want real data (reduced for speed)
    key_locations = [
        {"lat": 40.7128, "lng": -74.0060, "name": "NYC"},
        {"lat": 34.0522, "lng": -118.2437, "name": "LA"}, 
        {"lat": 39.9042, "lng": 116.4074, "name": "Beijing"},
        {"lat": 28.6139, "lng": 77.2090, "name": "Delhi"}
    ]
    
    for location in key_locations:
        try:
            aqi_data = get_air_quality(location["lat"], location["lng"])
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
                        "estimated": False,
                        "station_name": location["name"]
                    })
        except Exception as e:
            print(f"Error fetching real data for {location['name']}: {e}")
            continue
    
    return real_points




if __name__ == '__main__':
    app.run(debug=True, port=5000)