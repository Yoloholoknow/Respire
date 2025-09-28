# Respire - Air Quality Assistant with Personalized Health Recommendations

## Overview

Respire is an intelligent air quality assistant that provides real-time air quality data, health recommendations, and personalized advice based on your health profile. The application integrates with Auth0 for secure user authentication and allows users to create health profiles for personalized recommendations.

## Features

- **Real-time Air Quality Data**: Get current air quality information for any location
- **Interactive Map**: Visual representation of air quality with heatmaps
- **AI-Powered Health Recommendations**: General and personalized health advice using Google Gemini
- **User Authentication**: Secure login/logout with Auth0
- **Personalized Health Profiles**: Store age, medical conditions, allergies, and activity level
- **Forecast Data**: View air quality trends and predictions
- **Responsive Design**: Works on desktop and mobile devices

## Tech Stack

### Backend
- **Flask**: Python web framework
- **Google Gemini AI**: For generating health recommendations
- **Auth0**: JWT-based authentication
- **WAQI API**: Air quality data source
- **Flask-CORS**: Cross-origin resource sharing
- **python-jose**: JWT token verification

### Frontend
- **React**: User interface framework
- **Auth0 React SDK**: Authentication integration
- **Google Maps API**: Interactive maps and location services
- **Chart.js**: Data visualization
- **Tailwind CSS**: Styling and responsive design

## Setup Instructions

### Prerequisites
- Python 3.8+ (for backend)
- Node.js 16+ (for frontend)
- Auth0 account
- Google Cloud account (for Gemini AI and Maps API)
- WAQI API token (optional, has demo mode)

### Auth0 Setup

1. **Create Auth0 Application**:
   - Go to [Auth0 Dashboard](https://manage.auth0.com/)
   - Create a new Single Page Application
   - Note down your Domain and Client ID

2. **Configure Application Settings**:
   - **Allowed Callback URLs**: `http://localhost:5173, http://localhost:3000`
   - **Allowed Logout URLs**: `http://localhost:5173, http://localhost:3000`
   - **Allowed Web Origins**: `http://localhost:5173, http://localhost:3000`

3. **Create API**:
   - Go to APIs section in Auth0 Dashboard
   - Create a new API
   - Set Identifier (e.g., `http://localhost:5000`)
   - Note down the API Audience

### Backend Setup

1. **Navigate to backend directory**:
   ```bash
   cd backend
   ```

2. **Create virtual environment** (recommended):
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\\Scripts\\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Create environment file**:
   ```bash
   cp .env.example .env
   ```

5. **Configure environment variables** in `.env`:
   ```env
   # Auth0 Configuration
   AUTH0_DOMAIN=your-auth0-domain.auth0.com
   AUTH0_API_AUDIENCE=your-auth0-api-audience
   
   # API Keys
   WAQI_API_TOKEN=your-waqi-api-token
   GOOGLE_API_KEY=your-gemini-api-key
   
   # Flask Configuration
   FLASK_ENV=development
   FLASK_DEBUG=True
   ```

6. **Run the backend**:
   ```bash
   python app.py
   ```

The backend will start on `http://localhost:5000`

### Frontend Setup

1. **Navigate to frontend directory**:
   ```bash
   cd frontend
   ```

2. **Install dependencies**:
   ```bash
   npm install
   ```

3. **Create environment file**:
   ```bash
   cp .env.example .env
   ```

4. **Configure environment variables** in `.env`:
   ```env
   # Auth0 Configuration
   VITE_AUTH0_DOMAIN=your-auth0-domain.auth0.com
   VITE_AUTH0_CLIENT_ID=your-auth0-client-id
   VITE_AUTH0_AUDIENCE=your-auth0-api-audience
   
   # Google Maps API
   VITE_GOOGLE_MAPS_API_KEY=your-google-maps-api-key
   
   # Other API Keys
   VITE_WAQI_API_TOKEN=your-waqi-api-token
   ```

5. **Run the frontend**:
   ```bash
   npm run dev
   ```

The frontend will start on `http://localhost:5173`

## API Documentation

### Authentication Endpoints

#### GET /api/user/profile
Get the current user's health profile.

**Headers**: 
- `Authorization: Bearer <jwt_token>`

**Response**:
```json
{
  "age": 30,
  "medical_conditions": ["asthma"],
  "allergies": ["pollen", "dust"],
  "activity_level": "moderate"
}
```

#### POST /api/user/profile
Create or update user's health profile.

**Headers**: 
- `Authorization: Bearer <jwt_token>`
- `Content-Type: application/json`

**Body**:
```json
{
  "age": 30,
  "medical_conditions": ["asthma", "heart disease"],
  "allergies": ["pollen"],
  "activity_level": "high"
}
```

### Public Endpoints

#### POST /api/query
Get air quality information and health recommendations.

**Body**:
```json
{
  "prompt": "Air quality in New York"
}
```

**Response**: Includes general and personalized recommendations if user is authenticated.

#### POST /api/forecast
Get air quality forecast data for location.

**Body**:
```json
{
  "lat": 40.7128,
  "lng": -74.0060
}
```

## User Flow

1. **Guest Access**: Users can search for air quality information without authentication
2. **Login**: Click "Login" to authenticate with Auth0
3. **Health Profile**: After login, click "Health Profile" to set up personal information
4. **Personalized Recommendations**: Ask questions to receive personalized health advice
5. **Profile Management**: Update health information anytime through the profile form

## Personalized Health Features

The app provides personalized recommendations based on:

- **Age Groups**:
  - Children (0-12): Extra precautions and indoor activity suggestions
  - Teenagers (13-19): Activity modifications and awareness tips
  - Adults (20-64): Work and exercise recommendations
  - Seniors (65+): Enhanced safety measures and health monitoring

- **Medical Conditions**:
  - Asthma: Inhaler reminders, trigger avoidance
  - Heart Disease: Exercise limitations, medical consultation advice
  - COPD: Breathing techniques, oxygen level monitoring
  - Diabetes: Stress management, infection prevention

- **Activity Levels**:
  - Low: Indoor alternatives, gentle exercises
  - Moderate: Modified outdoor activities, timing recommendations
  - High: Performance impact warnings, alternative training suggestions

- **Allergies**: Specific pollen and environmental trigger warnings

## Security Features

- **JWT Authentication**: Secure token-based authentication with Auth0
- **Token Validation**: Backend verifies all JWT tokens using Auth0's public keys
- **User Isolation**: Each user's profile data is isolated and secure
- **HTTPS Ready**: Configurable for production HTTPS deployment
- **Environment Variables**: Sensitive configuration kept in environment files

## Development

### Backend Structure
```
backend/
├── app.py              # Main Flask application
├── requirements.txt    # Python dependencies
├── .env.example       # Environment template
└── .env              # Environment variables (not in git)
```

### Frontend Structure
```
frontend/
├── src/
│   ├── components/    # React components
│   ├── App.jsx       # Main application
│   └── main.jsx      # Entry point
├── package.json      # Node.js dependencies
├── .env.example     # Environment template
└── .env            # Environment variables (not in git)
```

### Key Components

- **UserProfileForm.jsx**: Health profile management interface
- **Auth0Provider**: Wraps the app for authentication context
- **JWT Verification**: Backend middleware for protected endpoints

## Production Deployment

1. **Environment Variables**: Set all production environment variables
2. **HTTPS**: Ensure Auth0 callbacks use HTTPS URLs
3. **CORS**: Configure CORS for production domains
4. **API Rate Limits**: Implement rate limiting for API endpoints
5. **Database**: Replace in-memory storage with persistent database
6. **Logging**: Add comprehensive logging for monitoring

## Troubleshooting

### Common Issues

1. **Auth0 Callback Errors**: Check callback URLs in Auth0 dashboard
2. **CORS Errors**: Verify frontend origin is allowed in backend CORS config
3. **JWT Verification Failures**: Ensure Auth0 domain and audience are correct
4. **API Key Errors**: Verify all API keys are correctly set in environment files

### Debug Mode

Enable debug mode by setting `FLASK_DEBUG=True` in backend `.env` file.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

This project is licensed under the MIT License.