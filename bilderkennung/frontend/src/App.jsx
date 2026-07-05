import React, { useState } from 'react';

function App() {
  const [image, setImage] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [description, setDescription] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleFileChange = async (event) => {
    const file = event.target.files[0];
    if (!file) return;

    // Reset state for new image
    setPreviewUrl(URL.createObjectURL(file));
    setDescription('');
    setError(null);
    setLoading(true);

    const formData = new FormData();
    formData.append('image', file);

    try {
      const response = await fetch('http://localhost:5060/api/analyze', {
        method: 'POST',
        body: formData,
      });

      const data = await response.json();

      if (data.error) {
        setError(data.error);
      } else {
        setDescription(data.description);
      }
    } catch (err) {
      setError('Fehler bei der Analyse: ' + err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-100 flex flex-col items-center py-10 px-4">
      <h1 className="text-3xl font-bold mb-8 text-gray-800">Bilderkennung</h1>
      
      <div className="bg-white p-6 rounded-lg shadow-md w-full max-w-2xl flex flex-col items-center">
        <input
          type="file"
          accept="image/*"
          onChange={handleFileChange}
          className="mb-6 block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-sm file:font-semibold file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100"
        />

        {previewUrl && (
          <img 
            src={previewUrl} 
            alt="Vorschau" 
            className="max-w-full h-auto rounded shadow-sm mb-6"
          />
        )}

        {loading && (
          <div className="text-blue-600 font-medium animate-pulse mb-4">
            Analysiere Bild...
          </div>
        )}

        {error && (
          <div className="text-red-500 bg-red-100 p-3 rounded w-full text-center mb-4">
            {error}
          </div>
        )}

        {description && !loading && (
          <div className="w-full text-gray-700 leading-relaxed bg-gray-50 p-4 rounded border border-gray-200">
            <h2 className="text-lg font-semibold mb-2 text-gray-800">Beschreibung:</h2>
            <p className="whitespace-pre-wrap">{description}</p>
          </div>
        )}
      </div>
    </div>
  );
}

export default App;