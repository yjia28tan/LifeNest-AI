// Deployment-specific settings. For Firebase Hosting set apiBaseUrl to the Cloud Run URL and authMode to "firebase".
const localDevelopment = ["localhost", "127.0.0.1"].includes(window.location.hostname);
window.LIFENEST_CONFIG = {
  apiBaseUrl: localDevelopment ? "http://localhost:8000" : "https://lifenest-api-ooaqekzvsq-as.a.run.app",
  // "dev" sends "Bearer dev:<random id>" (the API refuses this in prod); "firebase" uses anonymous Firebase Auth.
  authMode: localDevelopment ? "dev" : "firebase",
  firebase: {
    apiKey: "AIzaSyA_iSfj3S8W8b4Pf5CvYFtn6h0GmKw3dH4",
    authDomain: "lifenest-ai.firebaseapp.com",
    projectId: "lifenest-ai",
    appId: "1:94512454322:web:b523eb396dccbf32d0c7ab",
  },
};
