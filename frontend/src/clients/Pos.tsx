import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Banknote, CreditCard, Minus, Plus, Printer, Search, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { api, type Rec } from "../lib/api";
import { formatDateTime, formatMoney, formatQty } from "../lib/format";
import { useBootstrap, useToast } from "../lib/hooks";
import { Modal } from "../views/ActionDialog";

type CartLine = { product: Rec; qty: number };

export function PosTill() {
  const client = useQueryClient();
  const toast = useToast();
  const query = useQuery<any>({ queryKey: ["pos"], queryFn: () => api.get("/api/pos/bootstrap") });
  const [configId, setConfigId] = useState<number | null>(null);
  if (query.isLoading) return <div className="center-pad"><div className="spinner" role="status" aria-label="Loading" /></div>;
  if (query.error) return <div className="empty"><h2>The till could not be loaded</h2><p>{(query.error as Error).message}</p></div>;
  const data = query.data;
  const session = data.sessions.find((s: any) => s.config_id?.[0] === (configId ?? data.sessions[0]?.config_id?.[0]));
  async function open(id: number) {
    try {
      await api.post("/api/pos/session", { config_id: id });
      setConfigId(id);
      await client.invalidateQueries({ queryKey: ["pos"] });
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }
  if (!session) {
    return (
      <div className="client-page">
        <h1 className="page-title">Point of Sale</h1>
        <div className="tiles">
          {data.configs.map((c: any) => <div key={c.id} className="tile"><span className="tile-value tile-name">{c.display_name}</span><span className="muted">Closed</span><button className="btn btn-primary" onClick={() => open(c.id)}>Open session</button></div>)}
          {data.configs.length === 0 && <div className="empty"><p>Create a shop under Configuration first.</p></div>}
        </div>
      </div>
    );
  }
  return <Till session={session} products={data.products} taxes={data.taxes} onClosed={() => client.invalidateQueries({ queryKey: ["pos"] })} />;
}

function Till({ session, products, taxes, onClosed }: { session: Rec; products: Rec[]; taxes: Record<string, number>; onClosed: () => void }) {
  const toast = useToast();
  const client = useQueryClient();
  const { data: boot } = useBootstrap();
  const [cart, setCart] = useState<CartLine[]>([]);
  const [term, setTerm] = useState("");
  const [category, setCategory] = useState<number | null>(null);
  const [paying, setPaying] = useState(false);
  const [receipt, setReceipt] = useState<Rec | null>(null);
  const categories = useMemo(() => Array.from(new Map<number, string>(products.filter((p) => p.category_id).map((p) => [p.category_id[0], p.category_id[1]])).entries()), [products]);
  const shown = products.filter((p) => (!category || p.category_id?.[0] === category) && (!term || [p.name, p.default_code, p.barcode].some((v) => v?.toLowerCase().includes(term.toLowerCase()))));
  const add = (p: Rec, delta = 1) => setCart((c) => {
    const found = c.find((l) => l.product.id === p.id);
    if (!found) return delta > 0 ? [...c, { product: p, qty: delta }] : c;
    return c.map((l) => (l.product.id === p.id ? { ...l, qty: l.qty + delta } : l)).filter((l) => l.qty > 0);
  });
  // A preview only: the server computes the charged amounts from its own prices.
  const untaxed = cart.reduce((s, l) => s + l.qty * l.product.list_price, 0);
  const tax = cart.reduce((s, l) => s + l.qty * l.product.list_price * ((taxes[String(l.product.sale_tax_id?.[0])] ?? 0) / 100), 0);
  const total = Math.round((untaxed + tax) * 100) / 100;

  async function closeSession() {
    if (!window.confirm("Close this session and post its journal entry?")) return;
    try {
      await api.post(`/api/pos/session/${session.id}/close`);
      toast("Session closed and posted.");
      onClosed();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }
  async function pay(method: string, amountPaid?: number) {
    const res = await api.post("/api/pos/order", { session_id: session.id, payment_method: method, amount_paid: amountPaid, lines: cart.map((l) => ({ product_id: l.product.id, qty: l.qty })) });
    setCart([]);
    setPaying(false);
    setReceipt(res);
    client.invalidateQueries({ queryKey: ["pos"] });
  }
  return (
    <div className="pos">
      <section className="pos-products" aria-label="Products">
        <div className="pos-toolbar">
          <div className="search-box pos-search"><Search size={14} aria-hidden="true" /><input aria-label="Search products" placeholder="Search products..." value={term} onChange={(e) => setTerm(e.target.value)} /></div>
          <div className="seg" role="group" aria-label="Category">
            <button className={`btn btn-sm ${!category ? "btn-active" : ""}`} aria-pressed={!category} onClick={() => setCategory(null)}>All</button>
            {categories.map(([id, name]) => <button key={id} className={`btn btn-sm ${category === id ? "btn-active" : ""}`} aria-pressed={category === id} onClick={() => setCategory(id)}>{name}</button>)}
          </div>
          <span className="cp-spacer" />
          <span className="muted">{session.name} · {session.order_count} orders · {formatMoney(session.total_sales)}</span>
          <button className="btn btn-sm" onClick={closeSession}>Close session</button>
        </div>
        <div className="pos-grid">
          {shown.map((p) => (
            <button key={p.id} className="pos-product" onClick={() => add(p)}>
              <span className="pos-product-name">{p.name}</span>
              <span className="pos-product-price">{formatMoney(p.list_price)}</span>
              {p.product_type === "storable" && <span className={`pos-product-stock ${p.qty_available <= 0 ? "out" : ""}`}>{formatQty(p.qty_available)} in stock</span>}
            </button>
          ))}
          {shown.length === 0 && <div className="empty"><p>No products found. Mark products as available in POS first.</p></div>}
        </div>
      </section>
      <aside className="pos-cart" aria-label="Current order">
        <h2>Current order</h2>
        <div className="pos-lines">
          {cart.length === 0 && <p className="muted">Tap a product to add it.</p>}
          {cart.map((l) => (
            <div key={l.product.id} className="pos-line">
              <div className="pos-line-name">{l.product.name}<div className="muted">{formatMoney(l.product.list_price)}</div></div>
              <div className="pos-qty">
                <button className="icon-btn" aria-label={`Fewer ${l.product.name}`} onClick={() => add(l.product, -1)}>{l.qty === 1 ? <Trash2 size={14} /> : <Minus size={14} />}</button>
                <span>{l.qty}</span>
                <button className="icon-btn" aria-label={`More ${l.product.name}`} onClick={() => add(l.product, 1)}><Plus size={14} /></button>
              </div>
              <div className="pos-line-total">{formatMoney(l.qty * l.product.list_price)}</div>
            </div>
          ))}
        </div>
        <dl className="totals pos-totals"><div><dt>Untaxed</dt><dd>{formatMoney(untaxed)}</dd></div><div><dt>Tax</dt><dd>{formatMoney(tax)}</dd></div><div className="totals-final"><dt>Total</dt><dd>{formatMoney(total)}</dd></div></dl>
        <button className="btn btn-primary btn-block btn-lg" disabled={cart.length === 0} onClick={() => setPaying(true)}>Pay {formatMoney(total)}</button>
      </aside>
      {paying && <PayDialog total={total} onPay={pay} onClose={() => setPaying(false)} />}
      {receipt && <ReceiptDialog receipt={receipt} shop={boot?.company.name} onClose={() => setReceipt(null)} />}
    </div>
  );
}

function PayDialog({ total, onPay, onClose }: { total: number; onPay: (method: string, amount?: number) => Promise<void>; onClose: () => void }) {
  const [tendered, setTendered] = useState(String(total.toFixed(2)));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const change = Number(tendered) - total;
  async function run(method: string, amount?: number) {
    setBusy(true);
    setError("");
    try {
      await onPay(method, amount);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }
  return (
    <Modal title={`Payment: ${formatMoney(total)}`} onClose={onClose}>
      {error && <div className="banner banner-error" role="alert">{error}</div>}
      <label className="stack-field"><span>Cash tendered</span><input className="input num" type="number" step="0.01" min="0" value={tendered} onChange={(e) => setTendered(e.target.value)} /></label>
      <p>Change: <b>{change >= 0 ? formatMoney(change) : "Not enough"}</b></p>
      <div className="modal-footer">
        <button className="btn btn-primary" disabled={busy || change < 0} onClick={() => run("cash", Number(tendered))}><Banknote size={15} /> Cash</button>
        <button className="btn btn-primary" disabled={busy} onClick={() => run("card")}><CreditCard size={15} /> Card</button>
        <button className="btn" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}

function ReceiptDialog({ receipt, shop, onClose }: { receipt: Rec; shop?: string; onClose: () => void }) {
  return (
    <Modal title="Receipt" onClose={onClose}>
      <div className="receipt">
        <h3>{shop}</h3>
        <div className="muted">{receipt.name} · {formatDateTime(receipt.date_order)}</div>
        <table className="receipt-lines"><tbody>{receipt.line_ids.map((l: Rec) => <tr key={l.id}><td>{formatQty(l.qty)} x {l.product_id?.[1]}</td><td className="num">{formatMoney(l.price_subtotal)}</td></tr>)}</tbody></table>
        <dl className="totals"><div><dt>Tax</dt><dd>{formatMoney(receipt.amount_tax)}</dd></div><div className="totals-final"><dt>Total</dt><dd>{formatMoney(receipt.amount_total)}</dd></div>
          <div><dt>{receipt.payment_method === "cash" ? "Cash" : "Card"}</dt><dd>{formatMoney(receipt.amount_paid)}</dd></div>{receipt.amount_return > 0 && <div><dt>Change</dt><dd>{formatMoney(receipt.amount_return)}</dd></div>}</dl>
      </div>
      <div className="modal-footer no-print">
        <button className="btn btn-primary" onClick={onClose}>New order</button>
        <button className="btn" onClick={() => window.print()}><Printer size={15} /> Print</button>
      </div>
    </Modal>
  );
}
