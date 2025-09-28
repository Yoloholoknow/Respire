# Auth0 Setup Guide for Respire

## Step-by-Step Auth0 Configuration

### 1. Create Auth0 Account
- Go to [auth0.com](https://auth0.com) and sign up for a free account
- Complete the setup process

### 2. Create Application
1. In the Auth0 Dashboard, go to **Applications**
2. Click **Create Application**
3. Name it "Respire" (or any name you prefer)
4. Select **Single Page Web Applications**
5. Click **Create**

### 3. Configure Application Settings
Once created, go to the **Settings** tab and configure:

**Allowed Callback URLs**:
```
http://localhost:5173, http://localhost:3000
```

**Allowed Logout URLs**:
```
http://localhost:5173, http://localhost:3000
```

**Allowed Web Origins**:
```
http://localhost:5173, http://localhost:3000
```

**Allowed Origins (CORS)**:
```
http://localhost:5173, http://localhost:3000
```

### 4. Note Your Configuration
Copy these values from the Settings tab:
- **Domain**: (e.g., `dev-abc123.us.auth0.com`)
- **Client ID**: (e.g., `abc123def456ghi789`)

### 5. Create API
1. Go to **APIs** in the Auth0 Dashboard
2. Click **Create API**
3. Name it "Respire API"
4. Set Identifier to: `http://localhost:5000`
5. Set Signing Algorithm to: `RS256`
6. Click **Create**

### 6. Configure Scopes (Optional)
In your API settings, you can add custom scopes:
- `read:profile` - Read user profile
- `update:profile` - Update user profile

### 7. Update Environment Files

#### Backend (.env)
```env
AUTH0_DOMAIN=your-domain.auth0.com
AUTH0_API_AUDIENCE=http://localhost:5000
```

#### Frontend (.env)
```env
VITE_AUTH0_DOMAIN=your-domain.auth0.com
VITE_AUTH0_CLIENT_ID=your-client-id
VITE_AUTH0_AUDIENCE=http://localhost:5000
```

### 8. Test Authentication
1. Start your backend: `cd backend && python app.py`
2. Start your frontend: `cd frontend && npm run dev`
3. Open `http://localhost:5173`
4. Click the "Login" button
5. You should be redirected to Auth0 login page

## Troubleshooting

### Common Issues:

1. **"Callback URL mismatch"**
   - Ensure your callback URLs in Auth0 match your development server
   - Default React dev server runs on port 5173

2. **"Invalid audience"**
   - Make sure the audience in your frontend matches your API identifier
   - Check that both frontend and backend use the same audience value

3. **CORS errors**
   - Add your frontend URL to "Allowed Origins (CORS)" in Auth0
   - Ensure Flask-CORS is properly configured in backend

4. **Token verification fails**
   - Check that AUTH0_DOMAIN and AUTH0_API_AUDIENCE are set correctly
   - Ensure your Auth0 API uses RS256 algorithm

### Production Configuration

For production deployment, update the URLs:

**Auth0 Application Settings**:
- Replace `localhost` URLs with your production domain
- Use HTTPS URLs only

**Environment Variables**:
- Set production values for AUTH0_DOMAIN and other configs
- Never commit .env files to version control

## User Management

Auth0 provides a comprehensive user management system:

1. **Users & Roles**: Manage users in the Auth0 Dashboard
2. **Custom Claims**: Add custom user properties
3. **Social Logins**: Enable Google, Facebook, GitHub login
4. **Multi-Factor Auth**: Add extra security layers
5. **Passwordless**: Email/SMS login options

## Next Steps

Once Auth0 is configured:
1. Users can create accounts and log in
2. Set up health profiles for personalized recommendations
3. Receive customized air quality advice based on their health data
4. All user data is securely stored and managed