import {
  ArrowLeftRight, Banknote, BookOpen, Boxes, Calendar, Car, CheckSquare, Clock, Contact, CreditCard, Factory, FileSignature, FileText, Folder, Gauge, Kanban, Landmark, Layers, LayoutDashboard, LifeBuoy, type LucideIcon,
  Mail, MessageCircle, Palmtree, Receipt, Repeat, Settings, ShoppingBag, ShoppingCart, Store, Target, Ticket, Truck, UserCheck, UserPlus, Users, Wallet, Wrench, Circle,
} from "lucide-react";

const ICONS: Record<string, LucideIcon> = {
  "arrow-left-right": ArrowLeftRight, banknote: Banknote, "book-open": BookOpen, boxes: Boxes, calendar: Calendar, car: Car, "check-square": CheckSquare, clock: Clock, contact: Contact, "credit-card": CreditCard, factory: Factory,
  "file-signature": FileSignature, "file-text": FileText, folder: Folder, gauge: Gauge, kanban: Kanban, landmark: Landmark, layers: Layers, "layout-dashboard": LayoutDashboard, "life-buoy": LifeBuoy, mail: Mail, "message-circle": MessageCircle,
  palmtree: Palmtree, receipt: Receipt, repeat: Repeat, settings: Settings, "shopping-bag": ShoppingBag, "shopping-cart": ShoppingCart, store: Store, target: Target, ticket: Ticket, truck: Truck, "user-check": UserCheck, "user-plus": UserPlus, users: Users, wallet: Wallet, wrench: Wrench,
};

export function Icon({ name, size = 16 }: { name: string; size?: number }) {
  const C = ICONS[name] ?? Circle;
  return <C size={size} aria-hidden="true" />;
}
