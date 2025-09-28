import React, { useState, useEffect } from 'react';
import { useAuth0 } from '@auth0/auth0-react';

const UserProfileFormAuth0 = ({ userProfile, onProfileUpdate }) => {
    const { user, getAccessTokenSilently, isAuthenticated } = useAuth0();
    const [profile, setProfile] = useState({
        age: '',
        medical_conditions: [],
        allergies: [],
        activity_level: 'moderate'
    });
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [success, setSuccess] = useState('');

    // Initialize form with user metadata
    useEffect(() => {
        if (user && user.user_metadata) {
            setProfile({
                age: user.user_metadata.age || '',
                medical_conditions: user.user_metadata.medical_conditions || [],
                allergies: user.user_metadata.allergies || [],
                activity_level: user.user_metadata.activity_level || 'moderate'
            });
        }
    }, [user]);

    const handleInputChange = (field, value) => {
        setProfile(prev => ({
            ...prev,
            [field]: value
        }));
        setError('');
        setSuccess('');
    };

    const handleArrayInput = (field, value) => {
        // Convert comma-separated string to array
        const arrayValue = value.split(',').map(item => item.trim()).filter(item => item);
        setProfile(prev => ({
            ...prev,
            [field]: arrayValue
        }));
        setError('');
        setSuccess('');
    };

    const handleSubmit = async (e) => {
        e.preventDefault();
        
        if (!isAuthenticated) {
            setError('Please log in to save your profile');
            return;
        }

        if (!profile.age || isNaN(profile.age) || profile.age < 1 || profile.age > 120) {
            setError('Please enter a valid age between 1 and 120');
            return;
        }

        setLoading(true);
        setError('');
        setSuccess('');

        try {
            // Get Auth0 Management API token
            const token = await getAccessTokenSilently({
                authorizationParams: {
                    audience: `https://${import.meta.env.VITE_AUTH0_DOMAIN}/api/v2/`,
                    scope: "update:users update:users_app_metadata"
                }
            });

            // Update user metadata via Auth0 Management API
            const response = await fetch(`https://${import.meta.env.VITE_AUTH0_DOMAIN}/api/v2/users/${user.sub}`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                },
                body: JSON.stringify({
                    user_metadata: profile
                })
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.message || 'Failed to save profile');
            }

            const updatedUser = await response.json();
            setSuccess('Profile saved successfully!');
            
            // Notify parent component of the profile update
            if (onProfileUpdate) {
                onProfileUpdate(updatedUser.user_metadata);
            }

            // Clear success message after 3 seconds
            setTimeout(() => setSuccess(''), 3000);

        } catch (error) {
            console.error('Error saving profile:', error);
            setError(error.message || 'Failed to save profile. Please try again.');
        } finally {
            setLoading(false);
        }
    };

    if (!isAuthenticated) {
        return (
            <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4 text-center">
                <p className="text-yellow-800">Please log in to access your health profile and get personalized recommendations.</p>
            </div>
        );
    }

    return (
        <div className="bg-white rounded-lg shadow-md p-6 max-w-2xl mx-auto">
            <h2 className="text-2xl font-bold text-gray-800 mb-6">Health Profile</h2>
            <p className="text-gray-600 mb-6">
                Your health information is stored securely in your Auth0 profile and persists across sessions.
            </p>

            <form onSubmit={handleSubmit} className="space-y-6">
                {/* Age */}
                <div>
                    <label htmlFor="age" className="block text-sm font-medium text-gray-700 mb-2">
                        Age *
                    </label>
                    <input
                        type="number"
                        id="age"
                        min="1"
                        max="120"
                        value={profile.age}
                        onChange={(e) => handleInputChange('age', e.target.value)}
                        className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                        placeholder="Enter your age"
                        required
                    />
                </div>

                {/* Medical Conditions */}
                <div>
                    <label htmlFor="medical_conditions" className="block text-sm font-medium text-gray-700 mb-2">
                        Medical Conditions
                    </label>
                    <input
                        type="text"
                        id="medical_conditions"
                        value={profile.medical_conditions.join(', ')}
                        onChange={(e) => handleArrayInput('medical_conditions', e.target.value)}
                        className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                        placeholder="e.g., asthma, heart disease, diabetes (separate with commas)"
                    />
                </div>

                {/* Allergies */}
                <div>
                    <label htmlFor="allergies" className="block text-sm font-medium text-gray-700 mb-2">
                        Allergies
                    </label>
                    <input
                        type="text"
                        id="allergies"
                        value={profile.allergies.join(', ')}
                        onChange={(e) => handleArrayInput('allergies', e.target.value)}
                        className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                        placeholder="e.g., pollen, dust mites, pet dander (separate with commas)"
                    />
                </div>

                {/* Activity Level */}
                <div>
                    <label htmlFor="activity_level" className="block text-sm font-medium text-gray-700 mb-2">
                        Activity Level *
                    </label>
                    <select
                        id="activity_level"
                        value={profile.activity_level}
                        onChange={(e) => handleInputChange('activity_level', e.target.value)}
                        className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                        required
                    >
                        <option value="low">Low - Mostly sedentary, limited outdoor activities</option>
                        <option value="moderate">Moderate - Regular walking, some outdoor exercise</option>
                        <option value="high">High - Frequent outdoor sports, running, cycling</option>
                    </select>
                </div>

                {/* Error Message */}
                {error && (
                    <div className="bg-red-50 border border-red-200 rounded-md p-3">
                        <p className="text-red-800 text-sm">{error}</p>
                    </div>
                )}

                {/* Success Message */}
                {success && (
                    <div className="bg-green-50 border border-green-200 rounded-md p-3">
                        <p className="text-green-800 text-sm">{success}</p>
                    </div>
                )}

                {/* Submit Button */}
                <div className="flex justify-end">
                    <button
                        type="submit"
                        disabled={loading}
                        className={`px-6 py-2 rounded-md text-white font-medium ${
                            loading 
                                ? 'bg-gray-400 cursor-not-allowed' 
                                : 'bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2'
                        } transition-colors duration-200`}
                    >
                        {loading ? 'Saving...' : 'Save Profile'}
                    </button>
                </div>
            </form>

            {/* Data Storage Info */}
            <div className="mt-6 p-4 bg-blue-50 border border-blue-200 rounded-md">
                <h3 className="text-sm font-medium text-blue-800 mb-2">Data Storage</h3>
                <p className="text-sm text-blue-700">
                    Your health profile is stored as metadata in your Auth0 account and persists across sessions. 
                    This data is only used to provide personalized health recommendations.
                </p>
            </div>
        </div>
    );
};

export default UserProfileFormAuth0;