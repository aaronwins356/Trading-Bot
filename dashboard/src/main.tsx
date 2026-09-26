import { keepPreviousData, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";
import { initTheme } from "./theme";

initTheme();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // refetch keeps the frame: charts hold the previous render while new data loads
      placeholderData: keepPreviousData,
      refetchOnWindowFocus: false,
      retry: (count, err) => count < 2 && !(err instanceof Error && /40[134]/.test(err.message)),
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
