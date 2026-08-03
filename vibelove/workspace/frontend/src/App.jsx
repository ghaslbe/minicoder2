import React, { useState } from 'react';

const Navbar = () => (
  <nav className="bg-white border-b border-gray-100 sticky top-0 z-50">
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
      <div className="flex justify-between h-16 items-center">
        <div className="flex-shrink-0 flex items-center">
          <span className="text-[#B02AC9] font-bold text-xl tracking-tight">VIBELOVE</span>
        </div>
        <div className="hidden md:flex space-x-8 items-center">
          <a href="#fuer-wen" className="text-gray-600 hover:text-[#B02AC9] font-medium">Für wen</a>
          <a href="#features" className="text-gray-600 hover:text-[#B02AC9] font-medium">Lerninhalte</a>
          <a href="#so-gehts" className="text-gray-600 hover:text-[#B02AC9] font-medium">So geht's</a>
          <a href="#reviews" className="text-gray-600 hover:text-[#B02AC9] font-medium">Stimmen</a>
          <a href="#faq" className="text-gray-600 hover:text-[#B02AC9] font-medium">FAQ</a>
        </div>
        <a href="#anmeldung" className="bg-[#B02AC9] text-white px-4 md:px-5 py-2 rounded-full font-bold hover:bg-[#9a1fb3] transition-colors whitespace-nowrap">Gratis anmelden</a>
      </div>
    </div>
  </nav>
);

const Hero = () => (
  <section className="relative bg-gradient-to-b from-[#fdf4ff] to-white overflow-hidden pt-16 pb-20 lg:pt-24 lg:pb-32">
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
      <div className="lg:grid lg:grid-cols-2 lg:gap-12 items-center">
        <div className="text-left">
          <p className="inline-block bg-[#B02AC9]/10 text-[#B02AC9] px-3 py-1 rounded-full font-semibold text-sm mb-6">
            🎓 Kostenloser Online-Workshop – begrenzte Plätze
          </p>
          <h1 className="text-4xl md:text-5xl lg:text-6xl font-extrabold text-gray-900 leading-tight mb-6">
            In 90 Minuten zur produktiven KI – ohne IT-Abteilung
          </h1>
          <p className="text-lg md:text-xl text-gray-600 mb-8 leading-relaxed">
            Erfahren Sie in unserem kostenlosen Workshop, wie Sie mit Laguna KI-Modelle, Vektor-Datenbanken und RAG-Pipelines praktisch einsetzen – Schritt für Schritt erklärt, ohne Vorwissen.
          </p>
          <ul className="space-y-3 mb-8 text-gray-700">
            <li>✅ Live-Demo statt trockener Theorie</li>
            <li>✅ Schritt-für-Schritt-Anleitung für den sofortigen Start</li>
            <li>✅ Keine Vorkenntnisse nötig</li>
          </ul>
          <div className="flex flex-col sm:flex-row gap-4">
            <a href="#anmeldung" className="bg-[#B02AC9] text-white px-8 py-4 rounded-lg text-center font-bold text-lg hover:bg-[#9a1fb3] transition-all shadow-lg hover:shadow-xl transform hover:-translate-y-0.5">
              Jetzt kostenlos anmelden →
            </a>
            <a href="#fuer-wen" className="bg-white border-2 border-[#B02AC9] text-[#B02AC9] px-8 py-4 rounded-lg text-center font-bold text-lg hover:bg-[#fdf4ff] transition-all">
              Für wen ist das?
            </a>
          </div>
          <div className="flex items-center gap-3 mt-8 pt-6 border-t border-gray-200">
            <div className="flex -space-x-2">
              {["👩‍💻","👨‍💻","👩‍🔬","👨‍🎓"].map((e) => (
                <span key={e} className="w-10 h-10 rounded-full bg-gray-100 border-2 border-white flex items-center justify-center">{e}</span>
              ))}
            </div>
            <p className="text-sm text-gray-500">Schon <strong>1.200+</strong> Teilnehmer:innen haben teilgenommen</p>
          </div>
        </div>
        <div className="hidden lg:block relative">
          <div className="relative bg-white rounded-2xl shadow-2xl border border-gray-100 p-6 max-w-md ml-auto">
            <div className="flex items-center justify-between mb-6">
              <div>
                <p className="text-xs text-gray-400 font-medium uppercase tracking-wide">Mein Lernfortschritt</p>
                <p className="text-lg font-bold text-gray-900 mt-1">KI-Praktiker Kurs</p>
              </div>
              <span className="bg-green-100 text-green-700 text-xs font-bold px-3 py-1 rounded-full">● Aktiv</span>
            </div>
            <div className="bg-gray-50 rounded-xl p-4 mb-4">
              <div className="flex justify-between text-sm mb-2">
                <span className="font-medium text-gray-700">Prompt-Übungen</span>
                <span className="text-[#B02AC9] font-bold">3/5</span>
              </div>
              <div className="w-full bg-gray-200 rounded-full h-2">
                <div className="bg-[#B02AC9] h-2 rounded-full" style={{ width: '60%' }} />
              </div>
            </div>
            <div className="bg-gray-50 rounded-xl p-4 mb-4">
              <div className="flex justify-between text-sm mb-2">
                <span className="font-medium text-gray-700">RAG-Pipeline bauen</span>
                <span className="text-[#B02AC9] font-bold">1/7</span>
              </div>
              <div className="w-full bg-gray-200 rounded-full h-2">
                <div className="bg-[#B02AC9] h-2 rounded-full" style={{ width: '14%' }} />
              </div>
            </div>
            <div className="flex -space-x-3 mb-4">
              {["👩‍💻","👨‍💻","👩‍🔬"].map((e) => (
                <span key={e} className="w-9 h-9 rounded-full bg-gray-100 border-2 border-white flex items-center justify-center">{e}</span>
              ))}
              <span className="w-9 h-9 rounded-full bg-[#B02AC9] text-white text-xs font-bold border-2 border-white flex items-center justify-center">+12</span>
            </div>
            <p className="text-xs text-gray-500 text-center">
              🔒 Live-Ergebnisse aus dem Workshop · anonymisiert
            </p>
            <div className="absolute -top-4 -right-4 bg-yellow-400 text-gray-900 text-xs font-bold px-3 py-1.5 rounded-lg shadow-lg rotate-3">
              🎯 98 % würden weiterempfehlen
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
);

  const TargetAudience = () => (
    <section id="fuer-wen" className="py-20 bg-white">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="text-center mb-16">
          <h2 className="text-3xl md:text-4xl font-bold text-gray-900 mb-4">Für wen ist dieser Workshop?</h2>
          <div className="w-20 h-1 bg-[#B02AC9] mx-auto"></div>
        </div>
        <div className="grid md:grid-cols-3 gap-8">
          {[
            {
              title: 'Fach- & Führungskräfte',
              text: 'Sie möchten verstehen, wie KI Ihre Abläufe konkret verbessert — ohne sich in technische Details zu verlieren.',
              icon: '💼'
            },
            {
              title: 'Entwickler:innen & IT-Teams',
              text: 'Sie wollen KI-Modelle, Vektor-Datenbanken und RAG-Pipelines produktiv einsetzen — mit klarer Schritt-für-Schritt-Anleitung.',
              icon: '👩‍💻'
            },
            {
              title: 'Entscheider:innen',
              text: 'Sie planen KI-Projekte und brauchen fundierte Grundlagen, um die richtigen Investitionsentscheidungen zu treffen.',
              icon: '🎯'
            }
          ].map((item, idx) => (
            <div key={idx} className="p-8 bg-[#fdf4ff] rounded-xl border border-[#fcecfb] hover:shadow-md transition-shadow">
              <div className="text-4xl mb-4">{item.icon}</div>
              <h3 className="text-xl font-bold text-gray-900 mb-3">{item.title}</h3>
              <p className="text-gray-600">{item.text}</p>
            </div>
          ))}
        </div>
        <div className="text-center mt-12">
          <p className="text-lg text-gray-600 max-w-2xl mx-auto mb-6">
            <strong>Keine Vorkenntnisse nötig</strong> — nur Interesse und 90 Minuten Zeit.
          </p>
        </div>
      </div>
    </section>
  );

  const ModelCard = ({ name, params, description, benchmarks }) => (
    <div className="bg-[#fdf4ff] border border-[#fcecfb] rounded-2xl p-8 shadow-sm hover:shadow-md transition-shadow">
      <div className="mb-6">
        <h3 className="text-2xl font-bold text-gray-900">{name}</h3>
        <p className="text-sm font-semibold text-[#B02AC9] mt-1">{params}</p>
      </div>
      <p className="text-gray-600 text-sm leading-relaxed mb-6">{description}</p>
      <div className="bg-white rounded-xl p-5 border border-[#fcecfb]">
        <h4 className="text-xs font-bold uppercase tracking-wider text-gray-400 mb-3">Offizielle Poolside-Benchmarks</h4>
        {benchmarks.map((b) => (
          <div key={b.label} className="flex items-center justify-between gap-4 py-2.5 border-b border-gray-100 last:border-b-0">
            <span className="text-sm font-medium text-gray-600 flex-1">{b.label}</span>
            {b.detail && <span className="text-xs text-gray-400">{b.detail}</span>}
            <span className="text-lg font-extrabold text-[#B02AC9]">{b.value}</span>
          </div>
        ))}
      </div>
    </div>
  );

  const FoundationModels = () => (
    <section
      id="foundation-models"
      className="py-16 bg-white border-y border-gray-100"
    >
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="text-center mb-16">
          <h2 className="text-3xl font-bold text-gray-900 mb-4">
            Foundation Models
          </h2>
          <div className="w-20 h-1 bg-[#B02AC9] mx-auto mb-4"></div>
          <p className="text-lg text-gray-600 max-w-xl mx-auto">
            Moderne Anwendungen setzen auf leistungsstarke Foundation-Modelle —
            vortrainierte Netze, die als Generalisierungskern für vielfältige
            Aufgaben dienen. poolside.ai trainiert dafür die Laguna-Familie —
            agentische Coding-Modelle mit State-of-the-Art-Ergebnissen auf den
            öffentlichen Software-Benchmarks.
          </p>
        </div>
        <div className="grid md:grid-cols-2 gap-8 max-w-5xl mx-auto">
          <ModelCard
            name="Laguna S 2.1"
            params="118B-A8B · aktivierter MoE"
            description="Das leistungsstärkste Modell von poolside.ai für komplexe, mehrstufige Softwareentwicklung, tiefe Code-Architektur und agentische Tool-Nutzung."
            benchmarks={[
              { label: 'Terminal-Bench 2.1', value: '70.2' },
              { label: 'SWE-bench Multi', value: '78.5' },
              { label: 'SWE-bench Pro', value: '59.4' },
              { label: 'DeepSWE-bench', value: '40.4' },
              { label: 'SWE-bench Atlas', value: '46.2' },
              { label: 'Toolathlon', value: '49.7' },
            ]}
          />
          <ModelCard
            name="Laguna XS 2.1"
            params="33B-A3B · aktivierter MoE"
            description="Das effiziente Modell von poolside.ai für gängige Coding-Aufgaben mit herausragendem Preis-Leistungs-Verhältnis im agentischen Betrieb."
            benchmarks={[
              { label: 'SWE-bench Verified', value: '70.9', detail: 'XS 2.1 vs. XS.2: 69.9' },
              { label: 'SWE-bench Multi', value: '63.1', detail: 'XS 2.1 vs. XS.2: 57.7' },
              { label: 'SWE-bench Pro', value: '47.6', detail: 'XS 2.1 vs. XS.2: 46.3' },
              { label: 'Terminal-Bench 2.0', value: '37.5', detail: 'XS 2.1 vs. XS.2: 35.7' },
              { label: 'Terminal-Bench 2.1', value: '33.4' },
            ]}
          />
        </div>
        <p className="text-center text-sm text-gray-500 mt-10 max-w-2xl mx-auto">
          Benchmarks: Offizielle Evaluationen von poolside.ai auf den jeweils verlinkten
          öffentlichen Benchmark-Suiten (Stand: Modellrelease 2.1). Höhere Werte sind besser.
        </p>
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

const Reviews = () => (
  <section id="reviews" className="py-20 bg-[#fdf4ff]">
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
      <div className="text-center mb-16">
        <h2 className="text-3xl md:text-4xl font-bold text-gray-900 mb-4">Das sagen unsere Teilnehmer</h2>
        <div className="w-20 h-1 bg-[#B02AC9] mx-auto mb-4"></div>
        <p className="text-lg text-gray-600 max-w-2xl mx-auto">
          Über 1.200 Fachkräfte haben unseren Workshop bereits erfolgreich absolviert.
        </p>
      </div>
      <div className="grid md:grid-cols-3 gap-8">
        {[
          {
            name: 'Anna Weber',
            role: 'CTO, TechNova GmbH',
            quote: 'Der Workshop hat uns den Einstieg in Laguna enorm erleichtert. Innerhalb weniger Wochen konnten wir unsere erste RAG-Pipeline produktiv einsetzen — die Qualität der Inhalte ist herausragend.',
            stars: 5
          },
          {
            name: 'Markus Schneider',
            role: 'Lead Data Scientist, FinCore AG',
            quote: 'Endlich eine praxisnahe Einführung, die nicht nur Theorie vermittelt. Die Hands-on-Sessions mit echten Modellen haben mir sofort geholfen, die Architektur zu verstehen und in unserem Team anzuwenden.',
            stars: 5
          },
          {
            name: 'Julia Hoffmann',
            role: 'Head of AI, MedInsight Health',
            quote: 'Sehr gut strukturierter Workshop mit exzellenten Trainern. Besonders beeindruckt hat mich die Tiefe bei den Foundation Models — die Benchmarks und Vergleiche waren Gold wert.',
            stars: 5
          },
          {
            name: 'Thomas Berger',
            role: 'IT-Leiter, LogistikPro GmbH',
            quote: 'Selten einen so praxisorientierten Workshop erlebt. Die Übungen zur Modell-Orchestrierung konnte ich direkt in unserem Lagerverwaltungssystem umsetzen.',
            stars: 4
          },
          {
            name: 'Sandra Klein',
            role: 'Machine Learning Engineer, CloudScale Solutions',
            quote: 'Der Workshop hat mir die Augen geöffnet, wie einfach der Einstieg in eine moderne KI-Infrastruktur sein kann. Die Betreuung war erstklassig und sehr individuell.',
            stars: 5
          },
          {
            name: 'Michael Braun',
            role: 'Chief Architect, FinFlow Systems',
            quote: 'Fundiertes Know-how vom ersten Tag an. Besonders hilfreich fand ich die Hands-on-Sessions zu RAG-Pipelines – das hat uns Monate an Entwicklungszeit gespart.',
            stars: 5
          }
        ].map((review, idx) => (
          <div key={idx} className="bg-white rounded-2xl p-8 shadow-sm hover:shadow-md transition-shadow border border-[#fcecfb] flex flex-col">
            <div className="flex text-[#B02AC9] text-lg mb-4" aria-label={`${review.stars} von 5 Sternen`}>
              {'★'.repeat(review.stars)}
              {'☆'.repeat(5 - review.stars)}
            </div>
            <p className="text-gray-600 leading-relaxed mb-6 flex-grow italic">&ldquo;{review.quote}&rdquo;</p>
            <div className="pt-6 border-t border-gray-100">
              <div className="font-bold text-gray-900">{review.name}</div>
              <div className="text-sm text-[#B02AC9] font-medium mt-1">{review.role}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  </section>
);

const HowItWorks = () => (
  <section id="so-gehts" className="py-20 bg-gray-50">
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
      <div className="text-center mb-16">
        <h2 className="text-3xl md:text-4xl font-bold text-gray-900 mb-4">In 3 einfachen Schritten zu besseren Ergebnissen</h2>
        <div className="w-20 h-1 bg-[#B02AC9] mx-auto"></div>
        <p className="text-gray-600 mt-4 max-w-2xl mx-auto">Kein Vorwissen nötig — du startest direkt durch und siehst nach jedem Schritt Fortschritt.</p>
      </div>
      <div className="grid md:grid-cols-3 gap-8">
        <div className="bg-white p-8 rounded-2xl shadow-sm border border-gray-100 text-center">
          <div className="w-14 h-14 rounded-full bg-[#B02AC9] text-white text-2xl font-bold flex items-center justify-center mx-auto mb-6">1</div>
          <h3 className="text-xl font-semibold text-gray-900 mb-3">In 60 Sekunden anmelden</h3>
          <p className="text-gray-600">Nur Name und E-Mail — keine Kreditkarte, kein Risiko, sofortiger Zugang.</p>
        </div>
        <div className="bg-white p-8 rounded-2xl shadow-sm border border-gray-100 text-center">
          <div className="w-14 h-14 rounded-full bg-[#B02AC9] text-white text-2xl font-bold flex items-center justify-center mx-auto mb-6">2</div>
          <h3 className="text-xl font-semibold text-gray-900 mb-3">Live-Workshop in 90 Minuten</h3>
          <p className="text-gray-600">Gemeinsam mit den Coaches und anderen Teilnehmenden — Schritt für Schritt zum Profi-Ergebnis.</p>
        </div>
        <div className="bg-white p-8 rounded-2xl shadow-sm border border-gray-100 text-center">
          <div className="w-14 h-14 rounded-full bg-[#B02AC9] text-white text-2xl font-bold flex items-center justify-center mx-auto mb-6">3</div>
          <h3 className="text-xl font-semibold text-gray-900 mb-3">Sofort produktiv umsetzen</h3>
          <p className="text-gray-600">Direkt nach dem Workshop setzen Sie Ihr erstes KI-Projekt um — mit konkreten Ergebnissen, die Ihr Team begeistern.</p>
        </div>
      </div>
    </div>
  </section>
);

const FAQSection = () => (
  <section id="faq" className="py-20 bg-white">
    <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8">
      <h2 className="text-3xl md:text-4xl font-bold text-gray-900 text-center mb-12">
        Häufige Fragen
      </h2>
      <div className="space-y-6">
        {[
          {
            q: 'Brauche ich Vorkenntnisse?',
            a: 'Nein. Vibelove erklärt KI-Konzepte schrittweise und in einfacher Sprache. Sie lernen genau so schnell, wie Sie möchten — vom ersten Prompt bis zur eigenen KI-Anwendung.'
          },
          {
            q: 'Was kostet Vibelove?',
            a: 'Starten Sie kostenlos! Die Early-Access-Phase ist komplett kostenlos. Danach können Sie jederzeit auf einen kostenpflichtigen Plan upgraden oder einfach kündigen.'
          },
          {
            q: 'Wie schnell bekomme ich Zugang?',
            a: 'Nach der Anmeldung erhalten Sie sofort eine E-Mail mit Ihrem Zugang. Die durchschnittliche Antwortzeit unseres Teams liegt unter 24 Stunden.'
          },
          {
            q: 'Kann ich jederzeit kündigen?',
            a: 'Ja, absolut. Keine Kündigungsfrist, kein Admin-Aufwand — ein Klick genügt. Ihre Daten werden auf Wunsch komplett gelöscht.'
          }
        ].map((item) => (
          <details key={item.q} className="group bg-gray-50 rounded-xl p-6 transition-colors hover:bg-gray-100">
            <summary className="flex justify-between items-center cursor-pointer font-semibold text-gray-900 text-lg">
              {item.q}
              <span className="text-[#B02AC9] text-2xl transition-transform group-open:rotate-45">+</span>
            </summary>
            <p className="mt-4 text-gray-600 leading-relaxed">{item.a}</p>
          </details>
        ))}
      </div>
    </div>
  </section>
);

const CTASection = () => {
  const [submitted, setSubmitted] = useState(false);
  return (
    <section id="anmeldung" className="py-20 bg-gradient-to-br from-purple-50 to-white">
      <div className="max-w-xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
        <span className="inline-block bg-[#B02AC9]/10 text-[#B02AC9] px-4 py-1 rounded-full text-sm font-semibold mb-6">
          ⏳ Early Access — nur noch wenige Plätze
        </span>
        <h2 className="text-3xl md:text-4xl font-bold text-gray-900 mb-6">
          Jetzt 0€ sichern — später 49€/Monat
        </h2>
        <p className="text-lg text-gray-600 mb-6">
          Ihr Zugang ist heute kostenlos und bleibt es für immer — für alle Early-Access-Teilnehmer.
          Nach dem offiziellen Launch kostet Vibelove 49€/Monat.
        </p>
        <p className="text-sm font-medium text-[#B02AC9] mb-6">✓ Sofortiger Zugang ✓ Keine Kreditkarte ✓ Jederzeit kündbar</p>
        <p className="text-lg text-gray-600 mb-10 font-semibold">
          Die 90 Minuten Investition sparen Ihnen Monate an Trial &amp; Error.
        </p>
        <div className="bg-white p-8 rounded-xl shadow-md max-w-md mx-auto">
          {submitted ? (
            <div className="py-6">
              <div className="text-5xl mb-4">🎉</div>
              <h3 className="text-2xl font-bold text-gray-900 mb-2">Fast geschafft!</h3>
              <p className="text-gray-600 mb-6">
                Bitte bestätigen Sie die soeben gesendete E-Mail, um Ihren Zugang freizuschalten.
              </p>
              <p className="text-xs text-gray-500">✓ Keine Kreditkarte nötig ✓ Sofortiger Zugang</p>
            </div>
          ) : (
            <form onSubmit={(e) => { e.preventDefault(); setSubmitted(true); }} className="space-y-4">
              <input type="text" placeholder="Ihr Name" required
                     className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-[#B02AC9] focus:border-[#B02AC9]" />
              <input type="email" placeholder="Ihre E-Mail-Adresse" required
                     className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-[#B02AC9] focus:border-[#B02AC9]" />
              <button type="submit"
                      className="w-full bg-[#B02AC9] text-white py-3 rounded-lg font-bold hover:bg-[#9a1fb3] transition-colors shadow-md uppercase tracking-wide">
                Jetzt kostenlos starten →
              </button>
              <p className="text-xs text-gray-500 mt-2">✓ Keine Kreditkarte nötig ✓ Sofortiger Zugang</p>
            </form>
          )}
        </div>
      </div>
    </section>
  );
};

const Footer = () => (
  <footer className="bg-gray-900 text-white py-12">
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
      <div className="mb-8">
        <span className="text-[#B02AC9] font-bold text-xl tracking-tight">VIBELOVE</span>
      </div>
      <div className="flex justify-center space-x-6 text-gray-400 text-sm">
        <span className="cursor-default">Impressum</span>
        <span className="cursor-default">Datenschutz</span>
        <a href="mailto:hallo@vibelove.app" className="hover:text-white">Kontakt</a>
      </div>
      <p className="mt-8 text-gray-500 text-xs">
        &copy; {new Date().getFullYear()} Vibelove. Alle Rechte vorbehalten.
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
        <TargetAudience />
        <FoundationModels />
        <Features />
        <HowItWorks />
        <Reviews />
        <FAQSection />
        <CTASection />
      </main>
      <Footer />
    </div>
  );
}

export default App;