import React from 'react';
import './App.css';

function App() {
  return (
    <div className="app-container">
      {/* Hero Section */}
      <header className="hero">
        <div className="container hero-content">
          <h1>Meistere die Zukunft mit KI-Strategien</h1>
          <p>Erfahre in unserem exklusiven Live-Workshop, wie du modernste KI-Systeme nutzt, um deine Reichweite exponentiell zu steigern und digitale Prozesse zu automatisieren.</p>
          <a href="#anmeldung" className="btn btn-primary">Jetzt kostenlos Platz sichern</a>
        </div>
      </header>

      {/* Features Section */}
      <section className="features">
        <div className="container">
          <h2 style={{ textAlign: 'center', marginBottom: '50px', fontSize: '2.5rem' }}>Was dich erwartet</h2>
          <div className="feature-grid">
            <div className="feature-item">
              <h3>Automatisierte Workflows</h3>
              <p>Lerne, wie du tägliche Aufgaben durch intelligente KI-Agenten erheblich beschleunigst.</p>
            </div>
            <div className="feature-item">
              <h3>Content-Explosion</h3>
              <p>Entdecke Strategien, um mit minimalem Aufwand maximale Sichtbarkeit in sozialen Medien zu erzeugen.</p>
            </div>
            <div className="feature-item">
              <h3>Skalierbare Systeme</h3>
              <p>Wir zeigen dir, wie du KI-Tools so integrierst, dass sie für dein langfristiges Wachstum arbeiten.</p>
            </div>
          </div>
        </div>
      </section>

      {/* Image/Context Section */}
      <section className="context-section">
        <div className="container" style={{ display: 'flex', alignItems: 'center', gap: '40px', flexWrap: 'wrap' }}>
          <div style={{ flex: '1', minWidth: '300px' }}>
            <img 
              src="https://herr.tech/wp-content/uploads/2026/02/freepik__explosion-of-shortform-video-content-creationmulti__12847-1-768x429.png" 
              alt="KI Technologie" 
              style={{ width: '100%', borderRadius: '15px' }}
            />
          </div>
          <div style={{ flex: '1', minWidth: '300px' }}>
            <h2 style={{ fontSize: '2rem', marginBottom: '20px' }}>Warum dieses Webinar?</h2>
            <p>Die Welt verändert sich durch Künstliche Intelligenz schneller als je zuvor. Wer die Werkzeuge heute versteht, wird morgen die Märkte anführen.</p>
            <p>In diesem Workshop teilen wir praxiserprobte Methoden, die bereits tausende Follower generiert haben. Kein theoretisches Gerede, sondern echte Implementierung.</p>
          </div>
        </div>
      </section>

      {/* CTA / Registration Section */}
      <section id="anmeldung" className="cta-section">
        <div className="container">
          <div className="cta-box">
            <h2>Live-Test erfolgreich</h2>
            <p style={{ marginBottom: '30px' }}>Trage dich ein, um den Link zum Live-Event zu erhalten.</p>
            <form onSubmit={(e) => e.preventDefault()} style={{ textAlign: 'left' }}>
              <div className="form-group">
                <label>Vorname</label>
                <input type="text" placeholder="Dein Name" />
              </div>
              <div className="form-group">
                <label>E-Mail Adresse</label>
                <input type="email" placeholder="deine@mail.de" />
              </div>
              <button type="submit" className="btn btn-primary" style={{ width: '100%', marginTop: '10px' }}>Jetzt anmelden</button>
            </form>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer>
        <div className="container">
          <p>&copy; 2024 KI-Strategie Workshop. Alle Rechte vorbehalten.</p>
          <p>Impressum | Datenschutz</p>
        </div>
      </footer>
    </div>
  );
}

export default App;
