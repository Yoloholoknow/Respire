import { useState, useEffect, useRef } from 'react';
import { Line } from 'react-chartjs-2';
import { Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend } from 'chart.js';
import { useAuth0 } from '@auth0/auth0-react';
import LoginButton from './components/loginButton';
import LogoutButton from './components/logoutButton';
import Profile from './components/profile';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend);

function App() {
    const [messages, setMessages] = useState([]);
    const [input, setInput] = useState('');
    const [location, setLocation] = useState('');
    const mapRef = useRef(null);
    const [map, setMap] = useState(null);
    const [heatmap, setHeatmap] = useState(null);
    const [chartData, setChartData] = useState(null);
    const { user, isAuthenticated, loginWithRedirect, logout } = useAuth0();
    const [showProfile, setShowProfile] = useState(false);
    const profileRef = useRef(null);

    // simple debounce helper for map events
    const debounce = (func, wait = 300) => {
        let timeout;
        return (...args) => {
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(this, args), wait);
        };
    };

    const handleLocationChange = (e) => setLocation(e.target.value);

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
            const response = await fetch('/api/query', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ prompt: messageText }),
            });

            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

            const data = await response.json();
            const botMessage = { sender: 'bot', ...data.explanation };
            setMessages(prev => [...prev, botMessage]);

            if (map && data.coordinates) {
                const { lat, lng } = data.coordinates;
                const newCenter = new window.google.maps.LatLng(lat, lng);
                map.panTo(newCenter);
                map.setZoom(12);
            }

            const forecastResponse = await fetch('/api/forecast', { method: 'POST' });
            const forecastData = await forecastResponse.json();
            setChartData(forecastData);

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
                <div className="p-4 border-b border-gray-700 flex justify-between items-center">
                    <h1 className="text-2xl font-bold text-teal-400">AI Air Quality Assistant</h1>
                    <div className="flex space-x-2 items-center">
                        {!isAuthenticated ? (
                            <LoginButton />
                        ) : (
                            <>
                                <LogoutButton />
                                <Profile />
                            </>
                        )}
                    </div>
                </div>
                <div className="flex-1 p-4 overflow-y-auto">
                    {messages.map((msg, index) => (
                        <div key={index} className={`mb-4 flex ${msg.sender === 'user' ? 'justify-end' : 'justify-start'}`}>
                            <div className={`p-3 rounded-lg max-w-md ${msg.sender === 'user' ? 'bg-blue-600' : 'bg-gray-700'}`}>
                                {msg.sender === 'user' ? <p>{msg.text}</p> :
                                    (msg.overview ? (
                                        <div className="prose prose-invert">
                                            <h3>Current Air Quality Overview</h3>
                                            <ul>
                                                <li><strong>Overall AQI:</strong> {msg.overview.aqi} ({msg.overview.category})</li>
                                                <li><strong>Dominant Pollutant:</strong> {msg.overview.dominant_pollutant}</li>
                                                <li>{msg.overview.dominant_pollutant_description}</li>
                                                <li><strong>Health Summary:</strong> {msg.overview.health_summary}</li>
                                            </ul>
                                            <h3>Health Recommendations</h3>
                                            <ul>
                                                <li><strong>General Population:</strong> {msg.recommendations.general_population}</li>
                                                <li><strong>Sensitive Groups:</strong> {msg.recommendations.sensitive_groups}</li>
                                            </ul>
                                            <h3>Pollutant Breakdown</h3>
                                            <table className="w-full text-left">
                                                <thead>
                                                    <tr><th>Pollutant</th><th>AQI</th><th>Concentration</th></tr>
                                                </thead>
                                                <tbody>
                                                    {msg.pollutants.map((p, i) => <tr key={i}><td>{p.name}</td><td>{p.aqi}</td><td>{p.concentration}</td></tr>)}
                                                </tbody>
                                            </table>
                                        </div>
                                    ) : <p>{msg.text}</p>)
                                }
                            </div>
                        </div>
                    ))}
                    {chartData && (
                        <div className="bg-gray-700 p-4 rounded-lg mt-4">
                            <h3 className="text-xl font-bold mb-2">7-Day AQI Forecast</h3>
                            <Line data={chartData} />
                        </div>
                    )}
                </div>
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
            </div>
        </div>
    );
}

export default App;
