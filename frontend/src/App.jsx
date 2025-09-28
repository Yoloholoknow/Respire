import { useState, useEffect, useRef } from 'react';
import { Line } from 'react-chartjs-2';
import { Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend } from 'chart.js';
import { useAuth0 } from '@auth0/auth0-react';
import LoginButton from './components/loginButton';
import LogoutButton from './components/logoutButton';
import Profile from './components/profile';
import UserProfileForm from './components/UserProfileForm';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend);

function App() {
    const [messages, setMessages] = useState([]);
    const [input, setInput] = useState('');
    const [location, setLocation] = useState('');
    const mapRef = useRef(null);
    const [map, setMap] = useState(null);
    const [heatmap, setHeatmap] = useState(null);

    const { user, isAuthenticated, loginWithRedirect, logout, getAccessTokenSilently } = useAuth0();
    const [showProfile, setShowProfile] = useState(false);
    const [userProfile, setUserProfile] = useState(null);
    const [showProfileForm, setShowProfileForm] = useState(false);
    const profileRef = useRef(null);

    // simple debounce helper for map events
    const debounce = (func, wait = 300) => {
        let timeout;
        return (...args) => {
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(this, args), wait);
        };
    };

    // Get access token for API calls
    const getToken = async () => {
        if (!isAuthenticated) return null;
        try {
            return await getAccessTokenSilently({
                authorizationParams: {
                    audience: import.meta.env.VITE_AUTH0_AUDIENCE,
                    scope: "read:profile update:profile"
                }
            });
        } catch (error) {
            console.error('Error getting token:', error);
            return null;
        }
    };

    // Fetch user profile
    const fetchUserProfile = async () => {
        if (!isAuthenticated) return;
        try {
            const token = await getToken();
            if (!token) return;

            const response = await fetch('http://localhost:5000/api/user/profile', {
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                }
            });

            if (response.ok) {
                const data = await response.json();
                setUserProfile(data.profile);
            }
        } catch (error) {
            console.error('Error fetching user profile:', error);
        }
    };

    // Update user profile
    const updateUserProfile = async (profileData) => {
        if (!isAuthenticated) return;
        try {
            const token = await getToken();
            if (!token) return;

            const response = await fetch('http://localhost:5000/api/user/profile', {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(profileData)
            });

            if (response.ok) {
                const data = await response.json();
                setUserProfile(data.profile);
                setShowProfileForm(false);
                return true;
            }
        } catch (error) {
            console.error('Error updating user profile:', error);
        }
        return false;
    };    const handleLocationChange = (e) => setLocation(e.target.value);

    const handleSearch = async () => {
        if (location.trim() === '') return;
        try {
            const response = await fetch('/api/geocode', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ location }),
            });

            if (!response.ok) {
                const errData = await response.json();
                throw new Error(errData.error || `HTTP error! status: ${response.status}`);
            }

            const coordinates = await response.json();
            if (map && coordinates) {
                const { lat, lng } = coordinates;
                const newCenter = new window.google.maps.LatLng(lat, lng);
                map.panTo(newCenter);
                map.setZoom(12);
                
                // Fetch air quality data for the searched location and create a message
                try {
                    const aqiResponse = await fetch('/api/query', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ prompt: `Air quality in ${location}` }),
                    });
                    
                    if (aqiResponse.ok) {
                        const aqiData = await aqiResponse.json();
                        let searchMessage = { sender: 'bot', ...aqiData.explanation };
                        
                        // Fetch forecast data and add to message
                        try {
                            const forecastResponse = await fetch('/api/forecast', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ lat, lng }),
                            });
                            const forecastData = await forecastResponse.json();
                            searchMessage.chartData = forecastData;
                        } catch (forecastError) {
                            console.error("Failed to fetch forecast data:", forecastError);
                        }
                        
                        setMessages(prev => [...prev, searchMessage]);
                    }
                } catch (aqiError) {
                    console.error("Failed to fetch AQI data:", aqiError);
                }
            }
        } catch (error) {
            console.error("Failed to geocode location:", error);
            alert(`Could not find the location: ${error.message}`);
        }
        setLocation('');
    };

    const handleSendMessage = async (messageText) => {
        if (messageText.trim() === '') return;

        const userMessage = { sender: 'user', text: messageText };
        setMessages(prev => [...prev, userMessage]);
        setInput('');

        try {
            // Get auth token for authenticated requests
            const token = await getToken();
            const headers = { 'Content-Type': 'application/json' };
            if (token) {
                headers['Authorization'] = `Bearer ${token}`;
            }

            const response = await fetch('/api/query', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify({ prompt: messageText }),
            });

            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

            const data = await response.json();
            let botMessage = { sender: 'bot', ...data.explanation };

            // Only handle map and forecast for location-specific queries
            if (map && data.coordinates) {
                const { lat, lng } = data.coordinates;
                const newCenter = new window.google.maps.LatLng(lat, lng);
                map.panTo(newCenter);
                map.setZoom(12);
                
                // Fetch location-specific forecast data only when we have coordinates
                try {
                    const forecastResponse = await fetch('/api/forecast', {
                        method: 'POST',
                        headers: headers, // Use the same headers with auth token
                        body: JSON.stringify({ 
                            lat: data.coordinates.lat, 
                            lng: data.coordinates.lng 
                        }),
                    });
                    const forecastData = await forecastResponse.json();
                    // Add chart data directly to this message
                    botMessage.chartData = forecastData;
                } catch (forecastError) {
                    console.error("Failed to fetch forecast data:", forecastError);
                    // Don't show error to user for forecast failures
                }
            }

            setMessages(prev => [...prev, botMessage]);

        } catch (error) {
            console.error("Failed to send message:", error);
            const errorMessage = { sender: 'bot', text: "Sorry, I'm having trouble connecting. Please try again later." };
            setMessages(prev => [...prev, errorMessage]);
        }
    };

    // Normalize AQI to 0-1 for consistent heatmap coloring
    const normalizeAQI = (aqi, min = 0, max = 200) => Math.max(0, Math.min(1, (aqi - min) / (max - min)));

    useEffect(() => {
        const googleMapsScriptUrl = `https://maps.googleapis.com/maps/api/js?key=${import.meta.env.VITE_GOOGLE_MAPS_API_KEY}&libraries=visualization`;
        const script = document.createElement('script');
        script.src = googleMapsScriptUrl;
        script.async = true;
        script.defer = true;

        script.onload = async () => {
            const newMap = new window.google.maps.Map(mapRef.current, {
                center: { lat: 20, lng: 0 },
                zoom: 2,
                mapTypeControl: false,
                streetViewControl: false,
            });
            setMap(newMap);

            const newHeatmap = new window.google.maps.visualization.HeatmapLayer({
                data: [],
                map: newMap,
                // radius will be adjusted dynamically based on zoom
                radius: 20,
                opacity: 0.7,
                // allow heatmap to dissipate with zoom so points don't merge at low zoom
                dissipating: true,
                gradient: [
                    "rgba(0, 255, 0, 0)",     // Good
                    "rgba(0, 255, 0, 0.6)",   // Good
                    "rgba(255, 255, 0, 0.8)", // Moderate
                    "rgba(255, 165, 0, 0.9)", // Unhealthy for sensitive groups
                    "rgba(255, 0, 0, 1)",     // Unhealthy
                    "rgba(128, 0, 128, 1)",   // Very Unhealthy
                    "rgba(128, 0, 0, 1)"      // Hazardous
                ]
            });
            setHeatmap(newHeatmap);
            // Fetch heatmap points only for the current viewport bounds
            const fetchHeatmapForBounds = async (bounds) => {
                try {
                    if (!bounds) return;
                    const ne = bounds.getNorthEast();
                    const sw = bounds.getSouthWest();
                    const body = JSON.stringify({
                        sw: { lat: sw.lat(), lng: sw.lng() },
                        ne: { lat: ne.lat(), lng: ne.lng() }
                    });

                    const response = await fetch('/api/heatmap-data', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body
                    });
                    const data = await response.json();
                    const heatmapData = data.map(point => ({
                        location: new window.google.maps.LatLng(point.lat, point.lng),
                        weight: normalizeAQI(point.aqi)
                    }));
                    if (newHeatmap) newHeatmap.setData(heatmapData);
                } catch (error) {
                    console.error("Error fetching heatmap data:", error);
                }
            };

            // Initial fetch for the current view
            fetchHeatmapForBounds(newMap.getBounds());

            // Debounced fetch on idle (pan/zoom end)
            newMap.addListener('idle', debounce(() => {
                const bounds = newMap.getBounds();
                fetchHeatmapForBounds(bounds);
            }, 400));

            // Adjust radius dynamically on zoom changes so points don't merge when zoomed out
            newMap.addListener('zoom_changed', () => {
                const zoom = newMap.getZoom();
                // radius shrinks as you zoom out; clamp to reasonable range
                const radius = Math.max(2, Math.min(60, Math.round(40 - zoom * 2)));
                if (newHeatmap && newHeatmap.set) {
                    try {
                        newHeatmap.set('radius', radius);
                        // also update options to be safe
                        newHeatmap.setOptions({ radius });
                    } catch (e) {
                        // ignore if not supported in older API
                    }
                }
            });
        };

        script.onerror = () => console.error("Google Maps script failed to load.");
        document.head.appendChild(script);

        return () => { document.head.removeChild(script); };
    }, []);

    // Close profile card when clicking outside
    useEffect(() => {
        const handleOutsideClick = (e) => {
            if (!showProfile) return;
            if (profileRef.current && !profileRef.current.contains(e.target)) {
                setShowProfile(false);
            }
        };
        document.addEventListener('mousedown', handleOutsideClick);
        return () => document.removeEventListener('mousedown', handleOutsideClick);
    }, [showProfile]);

    return (
        <div className="flex h-screen w-screen font-sans bg-gray-900 text-white overflow-hidden">
            <div className="relative w-2/3 h-full">
                <div ref={mapRef} className="w-full h-full"></div>
                <div className="absolute top-4 left-4 bg-gray-800 p-2 rounded-lg shadow-lg">
                    <div className="flex">
                        <input
                            type="text"
                            value={location}
                            onChange={handleLocationChange}
                            onKeyPress={(e) => e.key === 'Enter' && handleSearch()}
                            placeholder="Search for a location..."
                            className="p-2 rounded-l-lg bg-gray-700 border border-gray-600 focus:outline-none focus:ring-2 focus:ring-teal-400"
                        />
                        <button
                            onClick={handleSearch}
                            className="px-4 py-2 bg-teal-500 rounded-r-lg hover:bg-teal-600 focus:outline-none focus:ring-2 focus:ring-teal-400 font-semibold"
                        >
                            Search
                        </button>
                    </div>
                </div>
            </div>
            <div className="w-1/3 h-full flex flex-col bg-gray-800 border-l border-gray-700">
                <div className="p-4 border-b border-gray-700">
                    {/* Top row with logo and profile */}
                    <div className="flex justify-between items-center mb-2">
                        <div className="flex items-center space-x-3">
                            <img src="/respire-logo.svg" alt="Respire Logo" className="rounded-full w-8 h-8" />
                            <h1 className="text-2xl font-bold text-teal-400">Respire</h1>
                        </div>
                        {isAuthenticated && <Profile />}
                    </div>
                    
                    {/* Bottom row with auth controls */}
                    <div className="flex justify-end items-center space-x-2">
                        {!isAuthenticated ? (
                            <LoginButton />
                        ) : (
                            <>
                                <button
                                    onClick={() => setShowProfileForm(!showProfileForm)}
                                    className="px-2 py-1 bg-blue-600 hover:bg-blue-700 rounded text-xs font-medium transition-colors"
                                    title="Manage your health profile for personalized recommendations"
                                >
                                    {showProfileForm ? '← Chat' : '⚕️ Health Profile'}
                                </button>
                                <LogoutButton />
                            </>
                        )}
                    </div>
                </div>
                <div className="flex-1 p-4 overflow-y-auto">
                    {showProfileForm ? (
                        <div className="h-full">
                            <UserProfileForm 
                                userProfile={userProfile} 
                                onProfileUpdate={setUserProfile}
                            />
                        </div>
                    ) : (
                        <>
                            {messages.length === 0 && (
                                <div className="mb-6">
                                    <h3 className="text-lg font-semibold text-white mb-3">Try asking me:</h3>
                                    <div className="grid grid-cols-1 gap-2">
                                        {[
                                            "What is AQI?",
                                            "Air quality in New York",
                                            "What should I do if I have asthma?",
                                            "How to protect myself from air pollution?",
                                            "Air quality in London",
                                            "Health advice for heart conditions"
                                        ].map((question, i) => (
                                            <button
                                                key={i}
                                                onClick={() => handleSendMessage(question)}
                                                className="text-left p-3 bg-gray-700 hover:bg-gray-600 rounded-lg text-sm text-gray-300 hover:text-white transition-colors"
                                            >
                                                💬 {question}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            )}
                    {messages.map((msg, index) => (
                        <div key={index} className={`mb-4 flex ${msg.sender === 'user' ? 'justify-end' : 'justify-start'}`}>
                            <div className={`p-4 rounded-lg ${msg.sender === 'user' ? 'bg-blue-600 max-w-md' : 'bg-gray-700 w-full'}`}>
                                {msg.sender === 'user' ? <p className="text-white">{msg.text}</p> :
                                    (msg.overview ? (
                                        <div className="space-y-4">
                                            {/* Header with AQI Badge */}
                                            <div className="flex items-center justify-between border-b border-gray-600 pb-3">
                                                <h3 className="text-lg font-bold text-white">Air Quality Report</h3>
                                                <div className={`px-3 py-1 rounded-full text-sm font-semibold ${
                                                    msg.overview.aqi <= 50 ? 'bg-green-500 text-white' :
                                                    msg.overview.aqi <= 100 ? 'bg-yellow-500 text-black' :
                                                    msg.overview.aqi <= 150 ? 'bg-orange-500 text-white' :
                                                    msg.overview.aqi <= 200 ? 'bg-red-500 text-white' :
                                                    msg.overview.aqi <= 300 ? 'bg-purple-500 text-white' :
                                                    'bg-red-900 text-white'
                                                }`}>
                                                    AQI {msg.overview.aqi} - {msg.overview.category}
                                                </div>
                                            </div>

                                            {/* Overview Section */}
                                            <div className="grid grid-cols-1 gap-3">
                                                <div className="bg-gray-600 p-3 rounded-lg">
                                                    <h4 className="font-semibold text-teal-400 mb-2">Overview</h4>
                                                    <p className="text-sm text-gray-300 mb-2">{msg.overview.health_summary}</p>
                                                    {msg.overview.dominant_pollutant && (
                                                        <div className="text-sm">
                                                            <span className="text-yellow-400 font-semibold">Main Concern: </span>
                                                            <span className="text-white">{msg.overview.dominant_pollutant}</span>
                                                            <p className="text-gray-300 mt-1">{msg.overview.dominant_pollutant_description}</p>
                                                        </div>
                                                    )}
                                                </div>
                                            </div>

                                            {/* Recommendations */}
                                            <div className="grid grid-cols-1 gap-3">
                                                <div className="bg-gray-600 p-3 rounded-lg">
                                                    <h4 className="font-semibold text-teal-400 mb-2">Health Recommendations</h4>
                                                    <div className="space-y-2 text-sm">
                                                        <div>
                                                            <span className="text-blue-400 font-semibold">General Public: </span>
                                                            <span className="text-gray-300">{msg.recommendations.general_population}</span>
                                                        </div>
                                                        <div>
                                                            <span className="text-orange-400 font-semibold">Sensitive Groups: </span>
                                                            <span className="text-gray-300">{msg.recommendations.sensitive_groups}</span>
                                                        </div>
                                                    </div>
                                                </div>

                                                {/* Personalized Recommendations */}
                                                {msg.personalized_recommendations && (
                                                    <div className="bg-gradient-to-r from-purple-600 to-blue-600 p-3 rounded-lg border-2 border-purple-400">
                                                        <h4 className="font-semibold text-white mb-2 flex items-center">
                                                            <span className="mr-2">👤</span>
                                                            Personalized for You
                                                        </h4>
                                                        {msg.personalized_recommendations.title && msg.personalized_recommendations.age_specific ? (
                                                            <div className="space-y-2 text-sm text-white">
                                                                {msg.personalized_recommendations.age_specific && msg.personalized_recommendations.age_specific.length > 0 && (
                                                                    <div>
                                                                        <span className="font-semibold text-yellow-300">Age-specific: </span>
                                                                        <span>{msg.personalized_recommendations.age_specific.join('; ')}</span>
                                                                    </div>
                                                                )}
                                                                {msg.personalized_recommendations.condition_specific && msg.personalized_recommendations.condition_specific.length > 0 && (
                                                                    <div>
                                                                        <span className="font-semibold text-red-300">Medical conditions: </span>
                                                                        <span>{msg.personalized_recommendations.condition_specific.join('; ')}</span>
                                                                    </div>
                                                                )}
                                                                {msg.personalized_recommendations.activity_specific && msg.personalized_recommendations.activity_specific.length > 0 && (
                                                                    <div>
                                                                        <span className="font-semibold text-green-300">Activity advice: </span>
                                                                        <span>{msg.personalized_recommendations.activity_specific.join('; ')}</span>
                                                                    </div>
                                                                )}
                                                            </div>
                                                        ) : msg.personalized_recommendations.advice ? (
                                                            <div className="text-sm text-white">
                                                                <p>{msg.personalized_recommendations.advice}</p>
                                                            </div>
                                                        ) : (
                                                            <div className="text-sm text-white">
                                                                <p className="mb-2">{msg.personalized_recommendations.message}</p>
                                                                <p className="text-yellow-300 font-semibold">{msg.personalized_recommendations.call_to_action}</p>
                                                            </div>
                                                        )}
                                                    </div>
                                                )}
                                            </div>

                                            {/* Detailed Pollutant Breakdown */}
                                            {msg.pollutants && msg.pollutants.length > 0 && (
                                                <div className="bg-gray-600 p-3 rounded-lg">
                                                    <h4 className="font-semibold text-teal-400 mb-3">Pollutant Details ({msg.pollutant_count || msg.pollutants.length} detected)</h4>
                                                    <div className="space-y-3">
                                                        {msg.pollutants.map((p, i) => (
                                                            <div key={i} className="bg-gray-700 p-3 rounded border-l-4 border-teal-400">
                                                                <div className="flex items-center justify-between mb-2">
                                                                    <h5 className="font-semibold text-white">{p.name}</h5>
                                                                    <div className="flex items-center space-x-2 text-sm">
                                                                        {p.aqi && (
                                                                            <span className={`px-2 py-1 rounded text-xs font-semibold ${
                                                                                p.aqi <= 50 ? 'bg-green-500 text-white' :
                                                                                p.aqi <= 100 ? 'bg-yellow-500 text-black' :
                                                                                p.aqi <= 150 ? 'bg-orange-500 text-white' :
                                                                                p.aqi <= 200 ? 'bg-red-500 text-white' :
                                                                                'bg-purple-500 text-white'
                                                                            }`}>
                                                                                AQI {p.aqi}
                                                                            </span>
                                                                        )}
                                                                        <span className="text-gray-300">{p.concentration}</span>
                                                                    </div>
                                                                </div>
                                                                {p.description && (
                                                                    <p className="text-sm text-gray-300 mb-1">{p.description}</p>
                                                                )}
                                                                {p.sources && (
                                                                    <p className="text-xs text-gray-400">
                                                                        <span className="font-semibold">Sources:</span> {p.sources}
                                                                    </p>
                                                                )}
                                                                {p.health_effects && (
                                                                    <p className="text-xs text-gray-400">
                                                                        <span className="font-semibold">Health Effects:</span> {p.health_effects}
                                                                    </p>
                                                                )}
                                                            </div>
                                                        ))}
                                                    </div>
                                                </div>
                                            )}

                                            {/* Data Source Info */}
                                            {msg.overview.data_source && (
                                                <div className="text-xs text-gray-500 border-t border-gray-600 pt-2">
                                                    Data source: {msg.overview.data_source === 'waqi' ? 'World Air Quality Index' : 'Estimated'}
                                                    {msg.overview.location && msg.overview.location !== 'Location data unavailable' && (
                                                        <span> • Location: {msg.overview.location}</span>
                                                    )}
                                                    {msg.overview.last_updated && (
                                                        <span> • Updated: {msg.overview.last_updated}</span>
                                                    )}
                                                </div>
                                            )}
                                        </div>
                                    ) : msg.type === 'educational' ? (
                                        <div className="space-y-4">
                                            <div className="flex items-center border-b border-gray-600 pb-3">
                                                <h3 className="text-lg font-bold text-white">{msg.title}</h3>
                                            </div>
                                            
                                            {/* AQI Definition */}
                                            <div className="bg-gray-600 p-3 rounded-lg">
                                                <h4 className="font-semibold text-teal-400 mb-2">Overview</h4>
                                                <p className="text-sm text-gray-300 mb-2">{msg.content.overview.definition}</p>
                                                <p className="text-sm text-gray-300 mb-2">{msg.content.overview.scale}</p>
                                                <p className="text-sm text-gray-300">{msg.content.overview.purpose}</p>
                                            </div>

                                            {/* AQI Categories */}
                                            <div className="bg-gray-600 p-3 rounded-lg">
                                                <h4 className="font-semibold text-teal-400 mb-3">AQI Categories</h4>
                                                <div className="space-y-2">
                                                    {msg.content.categories.map((category, i) => (
                                                        <div key={i} className="flex items-center justify-between p-2 bg-gray-700 rounded">
                                                            <div className="flex items-center space-x-3">
                                                                <span className={`w-4 h-4 rounded ${
                                                                    category.color === 'Green' ? 'bg-green-500' :
                                                                    category.color === 'Yellow' ? 'bg-yellow-500' :
                                                                    category.color === 'Orange' ? 'bg-orange-500' :
                                                                    category.color === 'Red' ? 'bg-red-500' :
                                                                    category.color === 'Purple' ? 'bg-purple-500' :
                                                                    'bg-red-900'
                                                                }`}></span>
                                                                <div>
                                                                    <span className="font-semibold text-white">{category.range}</span>
                                                                    <span className="ml-2 text-gray-300">{category.level}</span>
                                                                </div>
                                                            </div>
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>

                                            {/* Pollutants Info */}
                                            <div className="bg-gray-600 p-3 rounded-lg">
                                                <h4 className="font-semibold text-teal-400 mb-2">Key Pollutants</h4>
                                                <p className="text-sm text-gray-300 mb-2">{msg.content.pollutants.description}</p>
                                                <ul className="text-sm text-gray-300 space-y-1">
                                                    {msg.content.pollutants.list.map((pollutant, i) => (
                                                        <li key={i} className="flex items-center">
                                                            <span className="w-2 h-2 bg-teal-400 rounded-full mr-2"></span>
                                                            {pollutant}
                                                        </li>
                                                    ))}
                                                </ul>
                                            </div>
                                        </div>
                                    ) : msg.type === 'health_advice' ? (
                                        <div className="space-y-4">
                                            <div className="flex items-center border-b border-gray-600 pb-3">
                                                <h3 className="text-lg font-bold text-white">{msg.title}</h3>
                                            </div>
                                            
                                            <div className="bg-gray-600 p-3 rounded-lg">
                                                <h4 className="font-semibold text-teal-400 mb-2">General Advice</h4>
                                                <p className="text-sm text-gray-300">{msg.content.general_advice}</p>
                                            </div>

                                            <div className="bg-gray-600 p-3 rounded-lg">
                                                <h4 className="font-semibold text-teal-400 mb-3">Recommendations</h4>
                                                <ul className="text-sm text-gray-300 space-y-2">
                                                    {msg.content.recommendations.map((rec, i) => (
                                                        <li key={i} className="flex items-start">
                                                            <span className="w-2 h-2 bg-blue-400 rounded-full mr-2 mt-2"></span>
                                                            {rec}
                                                        </li>
                                                    ))}
                                                </ul>
                                            </div>

                                            {/* Personalized Recommendations for Educational Content */}
                                            {msg.personalized_recommendations && (
                                                <div className="bg-gradient-to-r from-purple-600 to-blue-600 p-3 rounded-lg border-2 border-purple-400">
                                                    <h4 className="font-semibold text-white mb-2 flex items-center">
                                                        <span className="mr-2">👤</span>
                                                        Personalized for You
                                                    </h4>
                                                    {msg.personalized_recommendations.advice ? (
                                                        <div className="text-sm text-white">
                                                            <p className="mb-2">{msg.personalized_recommendations.advice}</p>
                                                            {msg.personalized_recommendations.considerations && (
                                                                <div className="text-xs text-gray-200 mt-2 p-2 bg-black bg-opacity-20 rounded">
                                                                    Based on your profile: Age {msg.personalized_recommendations.considerations.age_group}, 
                                                                    Activity: {msg.personalized_recommendations.considerations.activity_level}
                                                                    {msg.personalized_recommendations.considerations.medical_conditions.length > 0 && 
                                                                        `, Conditions: ${msg.personalized_recommendations.considerations.medical_conditions.join(', ')}`
                                                                    }
                                                                </div>
                                                            )}
                                                        </div>
                                                    ) : (
                                                        <div className="text-sm text-white">
                                                            <p className="mb-2">{msg.personalized_recommendations.message}</p>
                                                            <p className="text-yellow-300 font-semibold">{msg.personalized_recommendations.call_to_action}</p>
                                                        </div>
                                                    )}
                                                </div>
                                            )}

                                            {msg.content.warning_signs && (
                                                <div className="bg-red-900 bg-opacity-20 border border-red-500 p-3 rounded-lg">
                                                    <h4 className="font-semibold text-red-400 mb-2">Warning Signs to Watch For</h4>
                                                    <ul className="text-sm text-gray-300 space-y-1">
                                                        {msg.content.warning_signs.map((sign, i) => (
                                                            <li key={i} className="flex items-center">
                                                                <span className="w-2 h-2 bg-red-400 rounded-full mr-2"></span>
                                                                {sign}
                                                            </li>
                                                        ))}
                                                    </ul>
                                                </div>
                                            )}

                                            {msg.content.sensitive_groups && (
                                                <div className="bg-gray-600 p-3 rounded-lg">
                                                    <h4 className="font-semibold text-teal-400 mb-2">Sensitive Groups</h4>
                                                    <div className="flex flex-wrap gap-2">
                                                        {msg.content.sensitive_groups.map((group, i) => (
                                                            <span key={i} className="px-3 py-1 bg-orange-500 text-white text-xs rounded-full">
                                                                {group}
                                                            </span>
                                                        ))}
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    ) : msg.type === 'general_advice' ? (
                                        <div className="space-y-4">
                                            <div className="flex items-center border-b border-gray-600 pb-3">
                                                <h3 className="text-lg font-bold text-white">{msg.title}</h3>
                                            </div>
                                            
                                            <div className="bg-gray-600 p-3 rounded-lg">
                                                <h4 className="font-semibold text-teal-400 mb-3">Indoor Protection Tips</h4>
                                                <ul className="text-sm text-gray-300 space-y-2">
                                                    {msg.content.indoor_tips.map((tip, i) => (
                                                        <li key={i} className="flex items-start">
                                                            <span className="w-2 h-2 bg-green-400 rounded-full mr-2 mt-2"></span>
                                                            {tip}
                                                        </li>
                                                    ))}
                                                </ul>
                                            </div>

                                            <div className="bg-gray-600 p-3 rounded-lg">
                                                <h4 className="font-semibold text-teal-400 mb-3">Outdoor Protection Tips</h4>
                                                <ul className="text-sm text-gray-300 space-y-2">
                                                    {msg.content.outdoor_tips.map((tip, i) => (
                                                        <li key={i} className="flex items-start">
                                                            <span className="w-2 h-2 bg-blue-400 rounded-full mr-2 mt-2"></span>
                                                            {tip}
                                                        </li>
                                                    ))}
                                                </ul>
                                            </div>

                                            <div className="bg-yellow-900 bg-opacity-20 border border-yellow-500 p-3 rounded-lg">
                                                <h4 className="font-semibold text-yellow-400 mb-2">When to Be Concerned</h4>
                                                <ul className="text-sm text-gray-300 space-y-1">
                                                    {msg.content.when_to_be_concerned.map((concern, i) => (
                                                        <li key={i} className="flex items-start">
                                                            <span className="w-2 h-2 bg-yellow-400 rounded-full mr-2 mt-2"></span>
                                                            {concern}
                                                        </li>
                                                    ))}
                                                </ul>
                                            </div>
                                        </div>
                                    ) : msg.type === 'pollutant_info' ? (
                                        <div className="space-y-4">
                                            <div className="flex items-center border-b border-gray-600 pb-3">
                                                <h3 className="text-lg font-bold text-white">{msg.title}</h3>
                                            </div>
                                            
                                            <div className="bg-gray-600 p-3 rounded-lg">
                                                <h4 className="font-semibold text-teal-400 mb-2">What is it?</h4>
                                                <p className="text-sm text-gray-300">{msg.content.what_is_it}</p>
                                                {msg.content.size_comparison && (
                                                    <p className="text-sm text-gray-300 mt-2 italic">{msg.content.size_comparison}</p>
                                                )}
                                            </div>

                                            {msg.content.health_effects && (
                                                <div className="bg-red-900 bg-opacity-20 border border-red-500 p-3 rounded-lg">
                                                    <h4 className="font-semibold text-red-400 mb-2">Health Effects</h4>
                                                    {Array.isArray(msg.content.health_effects) ? (
                                                        <ul className="text-sm text-gray-300 space-y-1">
                                                            {msg.content.health_effects.map((effect, i) => (
                                                                <li key={i} className="flex items-start">
                                                                    <span className="w-2 h-2 bg-red-400 rounded-full mr-2 mt-2"></span>
                                                                    {effect}
                                                                </li>
                                                            ))}
                                                        </ul>
                                                    ) : (
                                                        <div className="space-y-3">
                                                            {Object.entries(msg.content.health_effects).map(([key, effects]) => (
                                                                <div key={key}>
                                                                    <h5 className="text-red-300 font-medium capitalize mb-1">{key.replace('_', ' ')}</h5>
                                                                    <ul className="text-sm text-gray-300 space-y-1">
                                                                        {effects.map((effect, i) => (
                                                                            <li key={i} className="flex items-start ml-4">
                                                                                <span className="w-1 h-1 bg-red-400 rounded-full mr-2 mt-2"></span>
                                                                                {effect}
                                                                            </li>
                                                                        ))}
                                                                    </ul>
                                                                </div>
                                                            ))}
                                                        </div>
                                                    )}
                                                </div>
                                            )}

                                            {msg.content.safe_levels && (
                                                <div className="bg-green-900 bg-opacity-20 border border-green-500 p-3 rounded-lg">
                                                    <h4 className="font-semibold text-green-400 mb-2">Safe Levels</h4>
                                                    <div className="text-sm text-gray-300 space-y-1">
                                                        {Object.entries(msg.content.safe_levels).map(([level, description]) => (
                                                            <p key={level} className="flex items-start">
                                                                <span className="w-2 h-2 bg-green-400 rounded-full mr-2 mt-2"></span>
                                                                <strong className="text-green-300">{description}</strong>
                                                            </p>
                                                        ))}
                                                    </div>
                                                </div>
                                            )}

                                            {msg.content.protection && (
                                                <div className="bg-blue-900 bg-opacity-20 border border-blue-500 p-3 rounded-lg">
                                                    <h4 className="font-semibold text-blue-400 mb-2">Protection Tips</h4>
                                                    <ul className="text-sm text-gray-300 space-y-1">
                                                        {msg.content.protection.map((tip, i) => (
                                                            <li key={i} className="flex items-start">
                                                                <span className="w-2 h-2 bg-blue-400 rounded-full mr-2 mt-2"></span>
                                                                {tip}
                                                            </li>
                                                        ))}
                                                    </ul>
                                                </div>
                                            )}

                                            {msg.content.who_at_risk && (
                                                <div className="bg-orange-900 bg-opacity-20 border border-orange-500 p-3 rounded-lg">
                                                    <h4 className="font-semibold text-orange-400 mb-2">Who's at Risk</h4>
                                                    <div className="flex flex-wrap gap-2">
                                                        {msg.content.who_at_risk.map((group, i) => (
                                                            <span key={i} className="px-3 py-1 bg-orange-500 text-white text-xs rounded-full">
                                                                {group}
                                                            </span>
                                                        ))}
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    ) : msg.type === 'allergy_advice' ? (
                                        <div className="space-y-4">
                                            <div className="flex items-center border-b border-gray-600 pb-3">
                                                <h3 className="text-lg font-bold text-white">{msg.title}</h3>
                                            </div>
                                            
                                            <div className="bg-purple-900 bg-opacity-20 border border-purple-500 p-3 rounded-lg">
                                                <h4 className="font-semibold text-purple-400 mb-2">Air Pollution Connection</h4>
                                                <p className="text-sm text-gray-300 mb-2">{msg.content.air_pollution_connection}</p>
                                                <p className="text-sm text-yellow-300 font-semibold">{msg.content.double_trouble}</p>
                                            </div>

                                            {Object.entries(msg.content.management_strategies).map(([category, strategies]) => (
                                                <div key={category} className="bg-gray-600 p-3 rounded-lg">
                                                    <h4 className="font-semibold text-teal-400 mb-3 capitalize">{category.replace('_', ' ')} Strategies</h4>
                                                    <ul className="text-sm text-gray-300 space-y-2">
                                                        {strategies.map((strategy, i) => (
                                                            <li key={i} className="flex items-start">
                                                                <span className="w-2 h-2 bg-teal-400 rounded-full mr-2 mt-2"></span>
                                                                {strategy}
                                                            </li>
                                                        ))}
                                                    </ul>
                                                </div>
                                            ))}

                                            {msg.content.pollen_types && (
                                                <div className="bg-yellow-900 bg-opacity-20 border border-yellow-500 p-3 rounded-lg">
                                                    <h4 className="font-semibold text-yellow-400 mb-2">Pollen Seasons</h4>
                                                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                                                        {Object.entries(msg.content.pollen_types).map(([season, pollen]) => (
                                                            <div key={season} className="text-center p-2 bg-yellow-800 bg-opacity-30 rounded">
                                                                <div className="font-semibold text-yellow-300 capitalize">{season}</div>
                                                                <div className="text-xs text-gray-300">{pollen}</div>
                                                            </div>
                                                        ))}
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    ) : msg.type === 'indoor_air_advice' ? (
                                        <div className="space-y-4">
                                            <div className="flex items-center border-b border-gray-600 pb-3">
                                                <h3 className="text-lg font-bold text-white">{msg.title}</h3>
                                            </div>
                                            
                                            <div className="bg-blue-900 bg-opacity-20 border border-blue-500 p-3 rounded-lg">
                                                <h4 className="font-semibold text-blue-400 mb-2">Why It Matters</h4>
                                                <p className="text-sm text-gray-300">{msg.content.why_it_matters}</p>
                                            </div>

                                            <div className="bg-red-900 bg-opacity-20 border border-red-500 p-3 rounded-lg">
                                                <h4 className="font-semibold text-red-400 mb-2">Common Indoor Pollutants</h4>
                                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                                    {msg.content.common_indoor_pollutants.map((pollutant, i) => (
                                                        <div key={i} className="flex items-center text-sm text-gray-300">
                                                            <span className="w-2 h-2 bg-red-400 rounded-full mr-2"></span>
                                                            {pollutant}
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>

                                            {Object.entries(msg.content.improvement_strategies).map(([category, strategies]) => (
                                                <div key={category} className="bg-gray-600 p-3 rounded-lg">
                                                    <h4 className="font-semibold text-teal-400 mb-3 capitalize">{category.replace('_', ' ')}</h4>
                                                    <ul className="text-sm text-gray-300 space-y-2">
                                                        {strategies.map((strategy, i) => (
                                                            <li key={i} className="flex items-start">
                                                                <span className="w-2 h-2 bg-teal-400 rounded-full mr-2 mt-2"></span>
                                                                {strategy}
                                                            </li>
                                                        ))}
                                                    </ul>
                                                </div>
                                            ))}

                                            <div className="bg-green-900 bg-opacity-20 border border-green-500 p-3 rounded-lg">
                                                <h4 className="font-semibold text-green-400 mb-2">Plants That Help Clean Air</h4>
                                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                                    {msg.content.plants_that_help.map((plant, i) => (
                                                        <div key={i} className="text-sm text-gray-300 flex items-center">
                                                            <span className="text-green-400 mr-2">🌱</span>
                                                            {plant}
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>
                                        </div>
                                    ) : msg.type === 'wildfire_advice' ? (
                                        <div className="space-y-4">
                                            <div className="flex items-center border-b border-gray-600 pb-3">
                                                <h3 className="text-lg font-bold text-white">{msg.title}</h3>
                                            </div>
                                            
                                            <div className="bg-orange-900 bg-opacity-20 border border-orange-500 p-3 rounded-lg">
                                                <h4 className="font-semibold text-orange-400 mb-2">What is Wildfire Smoke?</h4>
                                                <p className="text-sm text-gray-300">{msg.content.what_is_wildfire_smoke}</p>
                                            </div>

                                            <div className="bg-red-900 bg-opacity-20 border border-red-500 p-3 rounded-lg">
                                                <h4 className="font-semibold text-red-400 mb-2">Health Effects</h4>
                                                <div className="space-y-3">
                                                    {Object.entries(msg.content.health_effects).map(([type, effects]) => (
                                                        <div key={type}>
                                                            <h5 className="text-red-300 font-medium capitalize mb-1">{type}</h5>
                                                            <ul className="text-sm text-gray-300 space-y-1">
                                                                {effects.map((effect, i) => (
                                                                    <li key={i} className="flex items-start ml-4">
                                                                        <span className="w-1 h-1 bg-red-400 rounded-full mr-2 mt-2"></span>
                                                                        {effect}
                                                                    </li>
                                                                ))}
                                                            </ul>
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>

                                            {Object.entries(msg.content.protection_strategies).map(([category, strategies]) => (
                                                <div key={category} className="bg-gray-600 p-3 rounded-lg">
                                                    <h4 className="font-semibold text-teal-400 mb-3">{category.replace('_', ' ').toUpperCase()}</h4>
                                                    <ul className="text-sm text-gray-300 space-y-2">
                                                        {strategies.map((strategy, i) => (
                                                            <li key={i} className="flex items-start">
                                                                <span className="w-2 h-2 bg-teal-400 rounded-full mr-2 mt-2"></span>
                                                                {strategy}
                                                            </li>
                                                        ))}
                                                    </ul>
                                                </div>
                                            ))}
                                        </div>
                                    ) : msg.type === 'protection_advice' || msg.type === 'exercise_advice' ? (
                                        <div className="space-y-4">
                                            <div className="flex items-center border-b border-gray-600 pb-3">
                                                <h3 className="text-lg font-bold text-white">{msg.title}</h3>
                                            </div>
                                            
                                            {Object.entries(msg.content).map(([key, value]) => {
                                                if (Array.isArray(value)) {
                                                    return (
                                                        <div key={key} className="bg-gray-600 p-3 rounded-lg">
                                                            <h4 className="font-semibold text-teal-400 mb-3 capitalize">{key.replace('_', ' ')}</h4>
                                                            <ul className="text-sm text-gray-300 space-y-2">
                                                                {value.map((item, i) => (
                                                                    <li key={i} className="flex items-start">
                                                                        <span className="w-2 h-2 bg-teal-400 rounded-full mr-2 mt-2"></span>
                                                                        {item}
                                                                    </li>
                                                                ))}
                                                            </ul>
                                                        </div>
                                                    );
                                                } else if (typeof value === 'object' && value !== null) {
                                                    return (
                                                        <div key={key} className="bg-gray-600 p-3 rounded-lg">
                                                            <h4 className="font-semibold text-teal-400 mb-3 capitalize">{key.replace('_', ' ')}</h4>
                                                            <div className="space-y-2">
                                                                {Object.entries(value).map(([subkey, subvalue]) => (
                                                                    <div key={subkey} className="text-sm">
                                                                        <strong className="text-white capitalize">{subkey.replace('_', ' ')}:</strong>
                                                                        {Array.isArray(subvalue) ? (
                                                                            <ul className="mt-1 ml-4 space-y-1">
                                                                                {subvalue.map((item, i) => (
                                                                                    <li key={i} className="text-gray-300 flex items-start">
                                                                                        <span className="w-1 h-1 bg-gray-400 rounded-full mr-2 mt-2"></span>
                                                                                        {item}
                                                                                    </li>
                                                                                ))}
                                                                            </ul>
                                                                        ) : (
                                                                            <span className="text-gray-300 ml-2">{subvalue}</span>
                                                                        )}
                                                                    </div>
                                                                ))}
                                                            </div>
                                                        </div>
                                                    );
                                                } else if (typeof value === 'string') {
                                                    return (
                                                        <div key={key} className="bg-blue-900 bg-opacity-20 border border-blue-500 p-3 rounded-lg">
                                                            <h4 className="font-semibold text-blue-400 mb-2 capitalize">{key.replace('_', ' ')}</h4>
                                                            <p className="text-sm text-gray-300">{value}</p>
                                                        </div>
                                                    );
                                                }
                                                return null;
                                            })}
                                        </div>
                                    ) : msg.type === 'ai_generated' ? (
                                        <div className="space-y-4">
                                            <div className="flex items-center border-b border-gray-600 pb-3">
                                                <h3 className="text-lg font-bold text-white">{msg.title}</h3>
                                            </div>
                                            
                                            <div className="bg-gradient-to-r from-blue-600 to-indigo-600 p-3 rounded-lg">
                                                <div className="text-sm text-white whitespace-pre-line">{msg.content.ai_response}</div>
                                                {msg.content.note && (
                                                    <div className="text-xs text-blue-200 mt-3 p-2 bg-black bg-opacity-20 rounded">
                                                        ℹ️ {msg.content.note}
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    ) : msg.type === 'out_of_domain' ? (
                                        <div className="space-y-4">
                                            <div className="flex items-center border-b border-gray-600 pb-3">
                                                <h3 className="text-lg font-bold text-white">{msg.title}</h3>
                                            </div>
                                            
                                            <div className="bg-yellow-900 bg-opacity-20 border border-yellow-500 p-3 rounded-lg">
                                                <p className="text-sm text-gray-300 mb-3">{msg.content.message}</p>
                                                <p className="text-sm text-yellow-300 font-semibold">{msg.content.redirect}</p>
                                            </div>

                                            <div className="bg-gray-600 p-3 rounded-lg">
                                                <h4 className="font-semibold text-teal-400 mb-3">What I Can Help With</h4>
                                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                                    {msg.content.what_i_can_help_with.map((item, i) => (
                                                        <div key={i} className="flex items-center text-sm text-gray-300">
                                                            <span className="w-2 h-2 bg-teal-400 rounded-full mr-2"></span>
                                                            {item}
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>

                                            <div className="bg-green-900 bg-opacity-20 border border-green-500 p-3 rounded-lg">
                                                <h4 className="font-semibold text-green-400 mb-2">Try These Questions</h4>
                                                <ul className="text-sm text-gray-300 space-y-1">
                                                    {msg.content.examples.map((example, i) => (
                                                        <li key={i} className="flex items-start cursor-pointer hover:text-green-300 transition-colors"
                                                            onClick={() => setInput(example)}>
                                                            <span className="w-2 h-2 bg-green-400 rounded-full mr-2 mt-2"></span>
                                                            "{example}"
                                                        </li>
                                                    ))}
                                                </ul>
                                            </div>
                                        </div>
                                    ) : <p className="text-white">{msg.text}</p>)
                                }

                                {/* Individual chart for location-specific responses */}
                                {msg.chartData && msg.overview && (
                                    <div className="bg-gray-700 p-4 rounded-lg mt-4">
                                        <h3 className="text-xl font-bold mb-2 text-white">7-Day AQI Forecast</h3>
                                        <Line data={msg.chartData} />
                                    </div>
                                )}
                            </div>
                        </div>
                    ))}
                        </>
                    )}
                </div>
                {!showProfileForm && (
                    <div className="p-4 border-t border-gray-700">
                        <div className="flex">
                            <input
                                type="text" value={input}
                                onChange={(e) => setInput(e.target.value)}
                                onKeyPress={(e) => e.key === 'Enter' && handleSendMessage(input)}
                                placeholder="Ask a follow-up question..."
                                className="flex-1 p-3 rounded-l-lg bg-gray-700 border border-gray-600 focus:outline-none focus:ring-2 focus:ring-teal-400"
                            />
                            <button
                                onClick={() => handleSendMessage(input)}
                                className="px-6 py-3 bg-teal-500 rounded-r-lg hover:bg-teal-600 focus:outline-none focus:ring-2 focus:ring-teal-400 font-semibold"
                            >
                                Send
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

export default App;
