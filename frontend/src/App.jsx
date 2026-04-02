import { Navigate, Route, Routes } from "react-router-dom";

import GenerationPage from "./pages/GenerationPage";
import IngestionPage from "./pages/IngestionPage";
import RetrievalPage from "./pages/RetrievalPage";
import StoragePage from "./pages/StoragePage";

export default function App() {
  return (
    <Routes>
      <Route element={<IngestionPage />} path="/" />
      <Route element={<StoragePage />} path="/storage" />
      <Route element={<RetrievalPage />} path="/retrieval" />
      <Route element={<GenerationPage />} path="/generation" />
      <Route element={<Navigate replace to="/" />} path="*" />
    </Routes>
  );
}
