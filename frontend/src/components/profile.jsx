import { useAuth0 } from "@auth0/auth0-react";

const Profile = () => {
    const { user, isAuthenticated } = useAuth0();
    return (
        isAuthenticated && (
            <div className="flex items-center space-x-2">
                {user?.picture && (
                    <img 
                        src={user.picture} 
                        alt={user?.name} 
                        className="w-8 h-8 rounded-full border-2 border-gray-600"
                    />
                )}
                {user?.name && (
                    <span className="text-sm text-gray-300 hidden sm:block">
                        {user.name.split(' ')[0]}
                    </span>
                )}
            </div>
        )
    )
}

export default Profile;