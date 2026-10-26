import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource/inter/latin-400.css';
import '@fontsource/inter/latin-500.css';
import '@fontsource/inter/latin-600.css';
import '@fontsource/inter/latin-700.css';
import '@fontsource-variable/noto-sans-sc';
import './runtime.css';
import { RuntimeApp } from './App';

createRoot(document.getElementById('root')!).render(<StrictMode><RuntimeApp /></StrictMode>);
