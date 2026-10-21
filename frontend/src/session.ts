import { createContext, useContext } from "react";
import type { Me } from "./api";

export const SessionContext = createContext<Me | null>(null);
export const useMe = () => useContext(SessionContext);
