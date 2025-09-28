import React, { useState, useEffect } from 'react';
import { useAuth0 } from '@auth0/auth0-react';

const UserProfileForm = ({ userProfile, onProfileUpdate }) => {
    const { getAccessTokenSilently, isAuthenticated } = useAuth0();
    const [profile, setProfile] = useState({
        age: '',
        medical_conditions: [],
        allergies: [],
        activity_level: 'moderate'
    });
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [success, setSuccess] = useState('');

    // Initialize form with existing profile data
    useEffect(() => {
        if (userProfile) {
            setProfile({
                age: userProfile.age || '',
                medical_conditions: userProfile.medical_conditions || [],
                allergies: userProfile.allergies || [],
                activity_level: userProfile.activity_level || 'moderate'
            });
        }
    }, [userProfile]);

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
            const token = await getAccessTokenSilently({
                authorizationParams: {
                    audience: import.meta.env.VITE_AUTH0_AUDIENCE,
                    scope: "read:profile write:profile"
                }
            });
            console.log('Token obtained for profile save:', !!token); // Debug log
            const response = await fetch('/api/user/profile', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                },
                body: JSON.stringify(profile)
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || 'Failed to save profile');
            }

            const savedProfile = await response.json();
            setSuccess('Profile saved successfully!');
            
            // Notify parent component of the profile update
            if (onProfileUpdate) {
                onProfileUpdate(savedProfile);
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
        <div className="bg-gray-800 rounded-lg shadow-md p-6 max-w-2xl mx-auto border border-gray-700">
            <h2 className="text-2xl font-bold text-white mb-6">Health Profile</h2>
            <p className="text-gray-300 mb-6">
                Share your health information to receive personalized air quality recommendations and health advice.
            </p>

            <form onSubmit={handleSubmit} className="space-y-6">
                {/* Age */}
                <div>
                    <label htmlFor="age" className="block text-sm font-medium text-white mb-2">
                        Age *
                    </label>
                    <input
                        type="number"
                        id="age"
                        min="1"
                        max="120"
                        value={profile.age}
                        onChange={(e) => handleInputChange('age', e.target.value)}
                        className="w-full px-3 py-2 bg-gray-700 border border-gray-600 text-white rounded-md focus:outline-none focus:ring-2 focus:ring-teal-400 focus:border-transparent placeholder-gray-400"
                        placeholder="Enter your age"
                        required
                    />
                </div>

                {/* Medical Conditions */}
                <div>
                    <label htmlFor="medical_conditions" className="block text-sm font-medium text-white mb-2">
                        Medical Conditions
                    </label>
                    <input
                        type="text"
                        id="medical_conditions"
                        value={profile.medical_conditions.join(', ')}
                        onChange={(e) => handleArrayInput('medical_conditions', e.target.value)}
                        className="w-full px-3 py-2 bg-gray-700 border border-gray-600 text-white rounded-md focus:outline-none focus:ring-2 focus:ring-teal-400 focus:border-transparent placeholder-gray-400"
                        placeholder="e.g., asthma, heart disease, diabetes (separate with commas)"
                    />
                    <p className="text-sm text-gray-400 mt-1">
                        List any medical conditions that might be affected by air quality. Leave blank if none.
                    </p>
                </div>

                {/* Allergies */}
                <div>
                    <label htmlFor="allergies" className="block text-sm font-medium text-white mb-2">
                        Allergies
                    </label>
                    <input
                        type="text"
                        id="allergies"
                        value={profile.allergies.join(', ')}
                        onChange={(e) => handleArrayInput('allergies', e.target.value)}
                        className="w-full px-3 py-2 bg-gray-700 border border-gray-600 text-white rounded-md focus:outline-none focus:ring-2 focus:ring-teal-400 focus:border-transparent placeholder-gray-400"
                        placeholder="e.g., pollen, dust mites, pet dander (separate with commas)"
                    />
                    <p className="text-sm text-gray-400 mt-1">
                        List any allergies that might be triggered by environmental factors. Leave blank if none.
                    </p>
                </div>

                {/* Activity Level */}
                <div>
                    <label htmlFor="activity_level" className="block text-sm font-medium text-white mb-2">
                        Activity Level *
                    </label>
                    <select
                        id="activity_level"
                        value={profile.activity_level}
                        onChange={(e) => handleInputChange('activity_level', e.target.value)}
                        className="w-full px-3 py-2 bg-gray-700 border border-gray-600 text-white rounded-md focus:outline-none focus:ring-2 focus:ring-teal-400 focus:border-transparent"
                        required
                    >
                        <option value="low">Low - Mostly sedentary, limited outdoor activities</option>
                        <option value="moderate">Moderate - Regular walking, some outdoor exercise</option>
                        <option value="high">High - Frequent outdoor sports, running, cycling</option>
                    </select>
                    <p className="text-sm text-gray-400 mt-1">
                        Your activity level helps us provide appropriate outdoor activity recommendations.
                    </p>
                </div>

                {/* Error Message */}
                {error && (
                    <div className="bg-red-900 bg-opacity-20 border border-red-500 rounded-md p-3">
                        <p className="text-red-400 text-sm">{error}</p>
                    </div>
                )}

                {/* Success Message */}
                {success && (
                    <div className="bg-green-900 bg-opacity-20 border border-green-500 rounded-md p-3">
                        <p className="text-green-400 text-sm">{success}</p>
                    </div>
                )}

                {/* Submit Button */}
                <div className="flex justify-end">
                    <button
                        type="submit"
                        disabled={loading}
                        className={`px-6 py-2 rounded-md text-white font-medium ${
                            loading 
                                ? 'bg-gray-600 cursor-not-allowed' 
                                : 'bg-teal-600 hover:bg-teal-700 focus:outline-none focus:ring-2 focus:ring-teal-400 focus:ring-offset-2 focus:ring-offset-gray-800'
                        } transition-colors duration-200`}
                    >
                        {loading ? 'Saving...' : 'Save Profile'}
                    </button>
                </div>
            </form>

            {/* Privacy Notice */}
            <div className="mt-6 p-4 bg-blue-900 bg-opacity-20 border border-blue-500 rounded-md">
                <h3 className="text-sm font-medium text-blue-400 mb-2">Privacy & Security</h3>
                <p className="text-sm text-blue-300">
                    Your health information is encrypted and stored securely. We only use this data to provide 
                    personalized air quality recommendations and will never share it with third parties.
                </p>
            </div>
        </div>
    );
};

export default UserProfileForm;