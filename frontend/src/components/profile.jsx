import { useAuth0 } from "@auth0/auth0-react";

// async function fetchData(user_id, age, isAsthmatic, hasCOPD) {
//   try {
//     let raw = JSON.stringify({
//     user_metadata: {
//         age: age,
//         isAsthmatic: isAsthmatic,
//         hasCOPD: hasCOPD
//     }
//     });

//     const response = await fetch(`https://dev-5tbkwrfnk6jlchv5.us.auth0.com/api/v2/users/${user_id}`, {
//       method: 'PATCH',
//       headers: {
//         authorization: 'Bearer ' + import.meta.env.VITE_APP_AUTH0_ACCESS_TOKEN
//       },
//       body: raw
//     });
//     if (!response.ok) {
//       throw new Error('Network response was not ok');
//     }
//     const data = await response.json();
//     console.log(data);
//   } catch (error) {
//     console.error('Error fetching data:', error);
//   }
// }

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
    // patchData("auth0|68d82918d385a7626b86e117", 25, true, true)

    if (isLoading) {
        return <div>Loading ...</div>;
    }

    return (
        isAuthenticated && (
            <article>
                {user?.picture && <img src={user.picture} alt={user?.name} />}
            </article>
        )
    )
}

export default Profile;