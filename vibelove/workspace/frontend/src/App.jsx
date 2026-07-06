import React from 'react';

const Navbar = () => (
  <nav className="bg-white border-b border-gray-100 sticky top-0 z-50">
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
      <div className="flex justify-between h-16 items-center">
        <div className="flex-shrink-0 flex items-center">
          <span className="text-[#B02AC9] font-bold text-xl tracking-tight">NEURAWORK</span>
        </div>
        <div className="hidden md:flex space-x-8">
          <a href="#features" className="text-gray-600 hover:text-[#B02AC9] font-medium">Vorteile</a>
          <a href="#details" className="text-gray-600 hover:text-[#B02AC9] font-medium">Details</a>
          <a href="#anmeldung" className="bg-[#B02AC9] text-white px-5 py-2 rounded-full font-bold hover:bg-[#9a1fb3] transition-colors">Jetzt anmelden</a>
        </div>
      </div>
    </div>
  </nav>
);

const Hero = () => (
  <section className="relative bg-gradient-to-b from-[#fdf4ff] to-white overflow-hidden pt-16 pb-20 lg:pt-24 lg:pb-32">
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
      <div className="lg:grid lg:grid-cols-2 lg:gap-12 items-center">
        <div className="text-left">
          <h1 className="text-4xl md:text-5xl lg:text-6xl font-extrabold text-gray-900 leading-tight mb-6">
            Operational AI: <span className="text-[#B02AC9]">From Tools to Infrastructure</span>
          </h1>
          <p className="text-lg md:text-xl text-gray-600 mb-8 leading-relaxed">
            Meistern Sie den Übergang von einfachen KI-Tools zu einer skalierbaren KI-Infrastruktur in Ihrem Unternehmen. Nehmen Sie an unserem exklusiven Workshop teil.
          </p>
          <div className="flex flex-col sm:flex-row gap-4">
            <a href="#anmeldung" className="bg-[#B02AC9] text-white px-8 py-4 rounded-lg text-center font-bold text-lg hover:bg-[#9a1fb3] transition-all shadow-lg">
              Kostenloser Workshop
            </a>
            <a href="#details" className="bg-white border-2 border-[#B02AC9] text-[#B02AC9] px-8 py-4 rounded-lg text-center font-bold text-lg hover:bg-[#fdf4ff] transition-all">
              Mehr erfahren
            </a>
          </div>
        </div>
        <div className="mt-12 lg:mt-0">
          <img 
            src="https://placehold.co/600x450/B02AC9/white?text=AI+Workshop+Hero" 
            alt="AI Workshop Hero" 
            className="rounded-2xl shadow-2xl w-full object-cover"
          />
        </div>
      </div>
    </div>
  </section>
);

const Features = () => (
  <section id="features" className="py-20 bg-white">
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
      <div className="text-center mb-16">
        <h2 className="text-3xl md:text-4xl font-bold text-gray-900 mb-4">Was Sie im Workshop lernen</h2>
        <div className="w-20 h-1 bg-[#B02AC9] mx-auto"></div>
      </div>
      <div className="grid md:grid-cols-3 gap-8">
        {[ 
          { title: 'Strategische Planung', text: 'Wie Sie KI-Projekte von der Idee bis zur Implementierung führen.', icon: '🚀' },
          { title: 'Infrastruktur-Aufbau', text: 'Skalierbare Systeme für den produktiven Einsatz von KI entwickeln.', icon: '🏗️' },
          { title: 'Operationalisierung', text: 'KI-Workflows in bestehende Geschäftsprozesse integrieren.', icon: '⚙️' }
        ].map((feature, idx) => (
          <div key={idx} className="p-8 bg-[#fdf4ff] rounded-xl border border-[#fcecfb] hover:shadow-md transition-shadow">
            <div className="text-4xl mb-4">{feature.icon}</div>
            <h3 className="text-xl font-bold text-gray-900 mb-3">{feature.title}</h3>
            <p className="text-gray-600">{feature.text}</p>
          </div>
        ))}
      </div>
    </div>
  </section>
);

const CTASection = () => (
  <section id="anmeldung" className="py-20 bg-gray-50">
    <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
      <h2 className="text-3xl md:text-4xl font-bold text-gray-900 mb-6">Sichern Sie sich Ihren Platz</h2>
      <p className="text-lg text-gray-600 mb-10">
        Die Plätze sind begrenzt. Melden Sie sich jetzt an, um keine Informationen zu verpassen.
      </p>
      <div className="bg-white p-8 rounded-2xl shadow-xl max-w-md mx-auto">
        <form className="space-y-4" onSubmit={(e) => e.preventDefault()}>
          <div>
            <label className="block text-left text-sm font-semibold text-gray-700 mb-1">Vorname & Nachname</label>
            <input type="text" className="w-full px-4 py-3 rounded-lg border border-gray-300 focus:ring-2 focus:ring-[#B02AC9] outline-none" placeholder="Max Mustermann" />
          </div>
          <div>
            <label className="block text-left text-sm font-semibold text-gray-700 mb-1">E-Mail Adresse</label>
            <input type="email" className="w-full px-4 py-3 rounded-lg border border-gray-300 focus:ring-2 focus:ring-[#B02AC9] outline-none" placeholder="max@beispiel.de" />
          </div>
          <button type="submit" className="w-full bg-[#B02AC9] text-white py-4 rounded-lg font-bold text-lg hover:bg-[#9a1fb3] transition-all">
            Jetzt kostenlos anmelden
          </button>
        </form>
      </div>
    </div>
  </section>
);

const Footer = () => (
  <footer className="bg-gray-900 text-white py-12">
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
      <div className="mb-8">
        <span className="text-[#B02AC9] font-bold text-xl tracking-tight">NEURAWORK</span>
      </div>
      <div className="flex justify-center space-x-6 text-gray-400 text-sm">
        <a href="#" className="hover:text-white">Impressum</a>
        <a href="#" className="hover:text-white">Datenschutz</a>
        <a href="#" className="hover:text-white">Kontakt</a>
      </div>
      <p className="mt-8 text-gray-500 text-xs">
        &copy; {new Date().getFullYear()} Neurawork. Alle Rechte vorbehalten.
      </p>
    </div>
  </footer>
);

function App() {
  return (
    <div className="min-h-screen flex flex-col">
      <Navbar />
      <main className="flex-grow">
        <Hero />
        <Features />
        <CTASection />
      </main>
      <Footer />
    </div>
  );
}

export default App;