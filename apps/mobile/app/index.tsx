import { Redirect } from "expo-router";

import { useAuth } from "../src/lib/auth";

export default function Index() {
  const { status } = useAuth();
  return <Redirect href={status === "signedIn" ? "/inbox" : "/sign-in"} />;
}
