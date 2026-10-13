// Deployment-specific settings. For Firebase Hosting set apiBaseUrl to the Cloud Run URL and authMode to "firebase".
window.LIFENEST_CONFIG = {
  apiBaseUrl: "http://localhost:8000",
  // "dev" sends "Bearer dev:<random id>" (the API refuses this in prod); "firebase" uses anonymous Firebase Auth.
  authMode: "dev",
  firebase: {
    apiKey: "",
    authDomain: "",
    projectId: "",
    appId: "",
  },
};
