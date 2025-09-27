import { useState, useEffect, useRef } from 'react';
import { Line } from 'react-chartjs-2';
import { Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend } from 'chart.js';
import ReactMarkdown from 'react-markdown';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend);

function App() {
    const [messages, setMessages] = useState([]);
    const [input, setInput] = useState('');
    const [location, setLocation] = useState('');
    const mapRef = useRef(null);
    const [map, setMap] = useState(null);
    const [heatmap, setHeatmap] = useState(null);
    const [chartData, setChartData] = useState(null);

    const debounce = (func, delay) => {
        let timeout;
        return function(...args) {
            const context = this;
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(context, args), delay);
        };
    };

    const handleLocationChange = (e) => {
        setLocation(e.target.value);
    };

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
        setMessages(prevMessages => [...prevMessages, userMessage]);
        setInput('');

        try {
            const response = await fetch('/api/query', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ prompt: messageText }),
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const data = await response.json();

            const botMessage = { sender: 'bot', ...data.explanation };
            setMessages(prevMessages => [...prevMessages, botMessage]);

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
            setMessages(prevMessages => [...prevMessages, errorMessage]);
        }
    };

    useEffect(() => {
        const googleMapsScriptUrl = `https://maps.googleapis.com/maps/api/js?key=${import.meta.env.VITE_GOOGLE_MAPS_API_KEY}&libraries=visualization`;
        const script = document.createElement('script');
        script.src = googleMapsScriptUrl;
        script.async = true;
        script.defer = true;

        script.onload = () => {
            const atlanta = { lat: 33.7490, lng: -84.3880 };
            const newMap = new window.google.maps.Map(mapRef.current, {
                center: atlanta,
                zoom: 8,
                mapTypeControl: false,
                streetViewControl: false,
            });
            setMap(newMap);

            const newHeatmap = new window.google.maps.visualization.HeatmapLayer({
                data: [], map: newMap, radius: 40, opacity: 0.7,
                gradient: [
                    'rgba(0, 255, 0, 0)', 'rgba(0, 255, 0, 1)', 'rgba(255, 255, 0, 1)',
                    'rgba(255, 140, 0, 1)', 'rgba(255, 0, 0, 1)', 'rgba(128, 0, 128, 1)'
                ]
            });
            setHeatmap(newHeatmap);

            newMap.addListener('idle', debounce(async () => {
                const bounds = newMap.getBounds();
                if(!bounds) return;
                const ne = bounds.getNorthEast();
                const sw = bounds.getSouthWest();

                try {
                    const response = await fetch('/api/heatmap-data', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            sw: { lat: sw.lat(), lng: sw.lng() },
                            ne: { lat: ne.lat(), lng: ne.lng() }
                        }),
                    });
                    const data = await response.json();
                    const heatmapData = data.map(point => ({
                        location: new window.google.maps.LatLng(point.lat, point.lng),
                        weight: point.aqi
                    }));
                    if (newHeatmap) newHeatmap.setData(heatmapData);
                } catch (error) {
                    console.error("Error fetching heatmap data:", error);
                }
            }, 1000));
        };

        script.onerror = () => console.error("Google Maps script failed to load.");
        document.head.appendChild(script);

        return () => { document.head.removeChild(script); };
    }, []);

    return (
        <div className="flex h-screen font-sans bg-gray-900 text-white">
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
                    <h1 className="text-2xl font-bold text-teal-400">AI Air Quality Assistant</h1>
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