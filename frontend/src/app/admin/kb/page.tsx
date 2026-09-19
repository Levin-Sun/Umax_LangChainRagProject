import KbAdmin from "@/components/KbAdmin";
import { api } from "@/lib/api";

export default function Page() {
  return <KbAdmin api={api} />;
}
