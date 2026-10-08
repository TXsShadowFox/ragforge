import { redirect } from "next/navigation";

/** proxy.ts sends "/" to the right page; this is only a fallback. */
export default function Home() {
  redirect("/documents");
}
