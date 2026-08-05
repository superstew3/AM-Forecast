import { jsx as _jsx } from "react/jsx-runtime";
import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";
const client = new QueryClient({
    defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});
createRoot(document.getElementById("root")).render(_jsx(React.StrictMode, { children: _jsx(QueryClientProvider, { client: client, children: _jsx(BrowserRouter, { children: _jsx(App, {}) }) }) }));
