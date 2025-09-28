import { useState } from "react";
import { useAuth0 } from "@auth0/auth0-react";

function patchData(user_id, age, isAsthmatic, hasCOPD) {
    var myHeaders = new Headers();
    myHeaders.append("Content-Type", "application/json");
    myHeaders.append("Accept", "application/json");
    myHeaders.append("authorization", `Bearer ${import.meta.env.VITE_APP_AUTH0_ACCESS_TOKEN}`)

    var raw = JSON.stringify({
        user_metadata: {age, isAsthmatic, hasCOPD}
    });

    var requestOptions = {
        method: 'PATCH',
        headers: myHeaders,
        body: raw,
        redirect: 'follow'
    };

fetch(`https://dev-5tbkwrfnk6jlchv5.us.auth0.com/api/v2/users/${user_id}`, requestOptions)
  .then(response => response.text())
  .then(result => console.log(result))
  .catch(error => console.log('error', error));
}

const Profile = () => {
    const { user, isAuthenticated, isLoading } = useAuth0();
    const [showPopup, setShowPopup] = useState(false);
    const [age, setAge] = useState("");
    const [isAsthmatic, setIsAsthmatic] = useState(false);
    const [hasCOPD, setHasCOPD] = useState(false);

    // patchData(user.sub, 25, true, true)


    if (isLoading) {
        return <div>Loading ...</div>;
    }

    const handleSubmit = () => {
        if (user) {
            patchData(user.sub, age, isAsthmatic, hasCOPD)
        }
        setShowPopup(false);
    }

    return (
        <>
        {isAuthenticated && (
            <article
                onClick={() => setShowPopup(true)}
                className="cursor-pointer"
                >
                {user?.picture && <img src={user.picture} alt={user?.name} />}
            </article>            
        )}

        {showPopup && (
          <div className="fixed inset-0 flex items-center justify-center bg-black bg-opacity-50 z-50">
            <div className="bg-white rounded-2xl p-6 shadow-lg w-80">
              <h2 className="text-lg font-semibold mb-4 text-gray-800">Enter Details</h2>

              <label className="block mb-2 text-gray-800">
                Age:
                <input
                  type="number"
                  value={age}
                  onChange={(e) => setAge(e.target.value)}
                  className="w-full border p-2 rounded mt-1"
                />
              </label>

              <label className="flex items-center mb-2  text-gray-800">
                <input
                  type="checkbox"
                  checked={isAsthmatic}
                  onChange={(e) => setIsAsthmatic(e.target.checked)}
                  className="mr-2"
                />
                Asthma
              </label>

              <label className="flex items-center mb-4  text-gray-800">
                <input
                  type="checkbox"
                  checked={hasCOPD}
                  onChange={(e) => setHasCOPD(e.target.checked)}
                  className="mr-2"
                />
                COPD
              </label>

              <div className="flex justify-end space-x-2">
                <button
                  onClick={() => setShowPopup(false)}
                  className="px-3 py-1 bg-gray-300 rounded hover:bg-gray-400"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSubmit}
                  className="px-3 py-1 bg-teal-500 text-white rounded hover:bg-teal-600"
                >
                  Enter
                </button>
              </div>
            </div>
          </div>
        )}

        </>
    )
}

export default Profile;