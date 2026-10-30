import { Link } from "react-router-dom";
import { useBootstrap } from "../lib/hooks";
import { Icon } from "./icons";

export function Home() {
  const { data } = useBootstrap();
  return (
    <div className="home">
      <div className="app-grid">
        {data?.apps.map((a) => (
          <Link key={a.key} className="app-tile" to={`/a/${a.key}`}>
            <span className="app-tile-icon" style={{ background: a.color }}><Icon name={a.icon} size={32} /></span>
            <span className="app-tile-name">{a.name}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}
