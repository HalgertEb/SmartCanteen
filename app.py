from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime, timedelta, date
from sqlalchemy import func
import csv
import io

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret-key-olimpiada-123' # В реальном проекте скрыть
# Настройка базы данных
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///canteen.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# --- МОДЕЛИ БАЗЫ ДАННЫХ (На основе рекомендаций Image 4) ---

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False) # 'student', 'cook', 'admin'
    allergies = db.Column(db.String(200), default="") # Для учеников
    subscription_end = db.Column(db.DateTime, nullable=True) # Дата окончания абонемента
    balance = db.Column(db.Float, default=0.0) # Баланс пользователя

class MenuItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50), nullable=False) # 'breakfast', 'lunch'
    quantity = db.Column(db.Integer, default=0) # Остатки продуктов
    date = db.Column(db.Date, default=date.today) # Дата актуальности
    allergens = db.Column(db.String(200), default="") # Аллергены
    is_active = db.Column(db.Boolean, default=False) # Статус публикации (False = Черновик, True = В меню)

class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    item_id = db.Column(db.Integer, db.ForeignKey('menu_item.id'))
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='reviews')
    item = db.relationship('MenuItem', backref='reviews')

class SupplyRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_name = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    priority = db.Column(db.String(20), nullable=False) # 'Urgent', 'Planned'
    status = db.Column(db.String(20), default='Pending') # 'Pending', 'Approved', 'Purchased'
    total_cost = db.Column(db.Float, default=0.0) # Стоимость закупки
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    message = db.Column(db.String(255), nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    item_id = db.Column(db.Integer, db.ForeignKey('menu_item.id'))
    status = db.Column(db.String(50), default="Paid") # 'Paid (One-time)', 'Paid (Subscription)'
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='orders')
    item = db.relationship('MenuItem', backref='orders')

# Связь с Flask-Login
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- ИНИЦИАЛИЗАЦИЯ БД (Важно для Render) ---
with app.app_context():
    db.create_all()
    # Автоматическое создание админа, если база пуста
    if not User.query.filter_by(role='admin').first():
        hashed_pw = generate_password_hash('admin', method='scrypt')
        admin = User(username='admin', password_hash=hashed_pw, role='admin')
        db.session.add(admin)
        db.session.commit()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ УВЕДОМЛЕНИЙ ---
def notify_user(user_id, message):
    notif = Notification(user_id=user_id, message=message)
    db.session.add(notif)

def notify_role(role, message):
    users = User.query.filter_by(role=role).all()
    for user in users:
        notify_user(user.id, message)

# --- МАРШРУТЫ ---

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        role = request.form['role'] # Выбор роли (для демо)

        hashed_pw = generate_password_hash(password, method='scrypt')
        new_user = User(username=username, password_hash=hashed_pw, role=role)
        
        try:
            db.session.add(new_user)
            db.session.commit()
            flash('Регистрация успешна! Войдите.', 'success')
            return redirect(url_for('login'))
        except:
            flash('Пользователь с таким именем уже существует.', 'error')

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Неверный логин или пароль', 'error')
    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    today = date.today()
    now = datetime.utcnow()
    
    # Логика для разных ролей
    if current_user.role == 'student':
        # Студент видит меню
        menu_items = MenuItem.query.filter(MenuItem.is_active == True, MenuItem.quantity > 0).all()
        is_subscribed = False
        if current_user.subscription_end and current_user.subscription_end > datetime.utcnow():
            is_subscribed = True
        
        # Проверка истечения абонемента (Уведомление)
        if is_subscribed:
            days_left = (current_user.subscription_end - datetime.utcnow()).days
            if 0 <= days_left <= 2:
                msg = f"Ваш абонемент истекает через {days_left} дн. Не забудьте продлить питание"
                # Простая проверка, чтобы не спамить (можно улучшить)
                if not Notification.query.filter_by(user_id=current_user.id, message=msg).first():
                    notify_user(current_user.id, msg)
                    db.session.commit()

        # Проверяем, что студент уже заказал сегодня (чтобы не показывать кнопку оплаты повторно)
        today_start = datetime.combine(today, datetime.min.time())
        tomorrow_start = today_start + timedelta(days=1)
        user_orders = Order.query.filter(Order.user_id == current_user.id, Order.timestamp >= today_start, Order.timestamp < tomorrow_start).all()
        ordered_item_ids = [o.item_id for o in user_orders]
            
        return render_template('dashboard.html', role='student', menu=menu_items, is_subscribed=is_subscribed, now=now, ordered_item_ids=ordered_item_ids)
    
    elif current_user.role == 'cook':
        # Повар видит управление меню и остатками
        # Разделяем склад на Продукты (сырье) и Готовые блюда
        warehouse_products = MenuItem.query.filter_by(category='product').all()
        
        # Разделяем блюда на Черновики (Склад) и Активные (Выдача)
        draft_dishes = MenuItem.query.filter(MenuItem.category.in_(['breakfast', 'lunch']), MenuItem.is_active == False).order_by(MenuItem.date.desc()).all()
        active_dishes = MenuItem.query.filter(MenuItem.category.in_(['breakfast', 'lunch']), MenuItem.is_active == True).order_by(MenuItem.quantity.desc()).all()
        
        pending_orders = Order.query.filter((Order.status.like('Paid%')) | (Order.status.like('Issued%'))).order_by(Order.timestamp).all()
        requests = SupplyRequest.query.order_by(SupplyRequest.created_at.desc()).all()
        
        # Получаем список уникальных названий блюд для автоподстановки
        dish_names = [r[0] for r in db.session.query(MenuItem.name).filter(MenuItem.category.in_(['breakfast', 'lunch'])).distinct().all()]
        
        return render_template('dashboard.html', role='cook', 
                               products=warehouse_products, 
                               draft_dishes=draft_dishes,
                               active_dishes=active_dishes,
                               orders=pending_orders, 
                               requests=requests, now=now,
                               dish_names=dish_names)
    
    elif current_user.role == 'admin':
        # Админ видит статистику
        today_start = datetime.combine(today, datetime.min.time())
        month_start = today.replace(day=1)
        
        # 1. Финансы
        revenue_today = db.session.query(func.sum(MenuItem.price)).join(Order).filter(Order.timestamp >= today_start, Order.status != 'Issued (Sub)').scalar() or 0
        revenue_month = db.session.query(func.sum(MenuItem.price)).join(Order).filter(Order.timestamp >= month_start, Order.status != 'Issued (Sub)').scalar() or 0
        
        # 2. Посещаемость
        portions_sold = Order.query.filter(Order.timestamp >= today_start).count()
        unique_students = db.session.query(func.count(func.distinct(Order.user_id))).filter(Order.timestamp >= today_start).scalar() or 0
        total_students = User.query.filter_by(role='student').count()
        
        # 3. Данные для блоков
        products = MenuItem.query.filter_by(category='product').all()
        dishes = MenuItem.query.filter(MenuItem.category.in_(['breakfast', 'lunch'])).all()
        pending_requests = SupplyRequest.query.filter_by(status='Pending').order_by(SupplyRequest.created_at.desc()).all()
        
        # 4. Отзывы с сортировкой
        sort_by = request.args.get('sort', 'newest')
        reviews_query = Review.query
        if sort_by == 'newest':
            reviews_query = reviews_query.order_by(Review.timestamp.desc())
        elif sort_by == 'oldest':
            reviews_query = reviews_query.order_by(Review.timestamp.asc())
        elif sort_by == 'rating_high':
            reviews_query = reviews_query.order_by(Review.rating.desc())
        elif sort_by == 'rating_low':
            reviews_query = reviews_query.order_by(Review.rating.asc())
        reviews = reviews_query.all()
        
        stats = {'revenue_today': revenue_today, 'revenue_month': revenue_month, 
                 'portions_sold': portions_sold, 'unique_students': unique_students, 'total_students': total_students}
        
        # 5. Данные для графика (последние 7 дней)
        chart_labels = []
        chart_data = []
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            d_start = datetime.combine(d, datetime.min.time())
            d_end = d_start + timedelta(days=1)
            day_rev = db.session.query(func.sum(MenuItem.price)).join(Order).filter(Order.timestamp >= d_start, Order.timestamp < d_end, Order.status != 'Issued (Sub)').scalar() or 0
            chart_labels.append(d.strftime('%d.%m'))
            chart_data.append(day_rev)
        
        return render_template('dashboard.html', role='admin', stats=stats, products=products, dishes=dishes, requests=pending_requests, reviews=reviews, now=now, chart_labels=chart_labels, chart_data=chart_data, sort_by=sort_by)

@app.route('/add_dish', methods=['POST'])
@login_required
def add_dish():
    if current_user.role != 'cook':
        return redirect(url_for('dashboard'))
    
    name = request.form['name']
    price = float(request.form['price'])
    category = request.form['category']
    qty = int(request.form['quantity'])
    allergens = request.form.get('allergens', '')
    
    # Обработка даты
    date_str = request.form.get('date')
    menu_date = date.today()
    if date_str:
        menu_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    
    # Проверяем, существует ли уже такое блюдо
    existing_item = MenuItem.query.filter_by(name=name, category=category).first()
    
    if existing_item:
        # Обновляем существующее блюдо
        existing_item.price = price
        existing_item.quantity += qty # Добавляем к текущему остатку
        existing_item.date = menu_date
        existing_item.allergens = allergens
        
        # Если блюдо уже активно, оставляем его активным (пополнение меню)
        # Если было неактивно (черновик или закончилось), то оставляем False (требует публикации)
        if not existing_item.is_active:
            existing_item.is_active = False 
    else:
        new_item = MenuItem(name=name, price=price, category=category, quantity=qty, date=menu_date, allergens=allergens, is_active=False)
        db.session.add(new_item)
        
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/buy/<int:item_id>')
@login_required
def buy(item_id):
    if current_user.role != 'student':
        return redirect(url_for('dashboard'))
    
    quantity = int(request.args.get('quantity', 1))
    item = MenuItem.query.get(item_id)
    
    if item and item.is_active and item.quantity is not None and item.quantity >= quantity:
        item.quantity -= quantity # Списываем со склада
        total_price = item.price * quantity
        
        # Проверка абонемента
        payment_status = "Issued" # Статус "Выдано" (или "Оформлено")
        if current_user.subscription_end and current_user.subscription_end > datetime.utcnow():
            payment_status = "Issued (Sub)"
            # Если есть подписка, списываем 0 (или можно реализовать логику лимитов)
        else:
            # Если подписки нет, списываем с баланса
            if current_user.balance < total_price:
                flash(f'Недостаточно средств! Стоимость: {total_price} ₽, Баланс: {current_user.balance} ₽', 'error')
                return redirect(url_for('dashboard'))
            current_user.balance -= total_price
            
        # Создаем заказ на каждую порцию (или можно изменить модель Order для хранения quantity)
        # Для простоты создаем N записей заказа, чтобы повар видел N карточек
        for _ in range(quantity):
            order = Order(user_id=current_user.id, item_id=item.id, status=payment_status, timestamp=datetime.utcnow())
            db.session.add(order)
            
        db.session.commit()
        flash(f'Блюдо {item.name} ({quantity} шт.) оформлено!', 'success')
        
        # Уведомление поварам о новом заказе
        notify_role('cook', f"Поступил новый заказ: {item.name} ({quantity} шт.)")
        
        # Проверка критического остатка
        if item.quantity < 5:
            notify_role('cook', f"Внимание! Заканчивается {item.name}. Осталось всего {item.quantity} порций")
    else:
        flash('Недостаточно товара на складе!', 'error')
    return redirect(url_for('dashboard'))

@app.route('/buy_subscription', methods=['POST'])
@login_required
def buy_subscription():
    if current_user.role != 'student':
        return redirect(url_for('dashboard'))
    
    price = 1499
    if current_user.balance >= price:
        current_user.balance -= price
        # Логика покупки абонемента (упрощенно продлеваем на 30 дней)
        current_user.subscription_end = datetime.utcnow() + timedelta(days=30)
        
        # Учитываем в финансах через скрытый товар
        sub_item = MenuItem.query.filter_by(name='Абонемент (30 дней)').first()
        if not sub_item:
            sub_item = MenuItem(name='Абонемент (30 дней)', price=price, category='service', quantity=999999, is_active=False)
            db.session.add(sub_item)
            db.session.commit() # Чтобы получить ID
            
        order = Order(user_id=current_user.id, item_id=sub_item.id, status='Paid (Subscription)', timestamp=datetime.utcnow())
        db.session.add(order)
        db.session.commit()
        flash('Абонемент успешно оформлен на 30 дней!', 'success')
    else:
        flash(f'Недостаточно средств. Стоимость: {price} ₽', 'error')
        
    return redirect(url_for('dashboard'))

@app.route('/top_up', methods=['POST'])
@login_required
def top_up():
    try:
        amount = float(request.form.get('amount'))
        if amount > 0:
            current_user.balance += amount
            db.session.commit()
            flash(f'Баланс пополнен на {amount} ₽', 'success')
    except ValueError:
        flash('Некорректная сумма', 'error')
    return redirect(url_for('dashboard'))

@app.route('/update_profile', methods=['POST'])
@login_required
def update_profile():
    if request.method == 'POST':
        allergies = request.form.get('allergies')
        current_user.allergies = allergies
        db.session.commit()
        flash('Данные о здоровье обновлены.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/add_review/<int:item_id>', methods=['POST'])
@login_required
def add_review(item_id):
    rating = int(request.form.get('rating'))
    comment = request.form.get('comment')
    new_review = Review(user_id=current_user.id, item_id=item_id, rating=rating, comment=comment)
    db.session.add(new_review)
    db.session.commit()
    notify_role('admin', f"Поступил новый отзыв от ученика (Оценка: {rating}/5). Проверьте вкладку отзывов")
    flash('Спасибо за отзыв!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/change_password', methods=['POST'])
@login_required
def change_password():
    old_password = request.form.get('old_password')
    new_password = request.form.get('new_password')
    
    if check_password_hash(current_user.password_hash, old_password):
        current_user.password_hash = generate_password_hash(new_password, method='scrypt')
        db.session.commit()
        flash('Пароль успешно изменен.', 'success')
    else:
        flash('Старый пароль введен неверно.', 'error')
    return redirect(url_for('dashboard'))

# --- API ДЛЯ ПОВАРА ---

@app.route('/api/get_orders')
@login_required
def get_orders():
    if current_user.role != 'cook':
        return jsonify({'error': 'Unauthorized'}), 403
    
    # Получаем все активные заказы (Оплаченные или Оформленные, но не Выполненные)
    orders = Order.query.filter((Order.status.like('Paid%')) | (Order.status.like('Issued%'))).order_by(Order.timestamp).all()
    
    orders_data = []
    for order in orders:
        orders_data.append({
            'id': order.id,
            'username': order.user.username,
            'item_name': order.item.name,
            'timestamp': order.timestamp.strftime('%H:%M'),
            'status': order.status
        })
    return jsonify(orders_data)

@app.route('/complete_order/<int:order_id>', methods=['POST'])
@login_required
def complete_order(order_id):
    if current_user.role != 'cook': return jsonify({'error': 'Unauthorized'}), 403
    order = Order.query.get(order_id)
    if order:
        order.status = 'Completed'
        db.session.commit()
        notify_user(order.user_id, f"Ваш заказ '{order.item.name}' готов к выдаче!")
        return jsonify({'success': True})
    return jsonify({'error': 'Order not found'}), 404

@app.route('/complete_all_orders', methods=['POST'])
@login_required
def complete_all_orders():
    if current_user.role != 'cook': return jsonify({'error': 'Unauthorized'}), 403
    
    orders = Order.query.filter((Order.status.like('Paid%')) | (Order.status.like('Issued%'))).all()
    for order in orders:
        order.status = 'Completed'
    db.session.commit()
    # В идеале уведомлять каждого пользователя, но для массовой операции можно упростить или в цикле
    return jsonify({'success': True})

@app.route('/api/get_notifications')
@login_required
def get_notifications():
    # Получаем последние 10 уведомлений (сначала непрочитанные, потом новые)
    notifications = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.is_read.asc(), Notification.created_at.desc()).limit(10).all()
    unread_count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    
    data = [{
        'id': n.id,
        'message': n.message,
        'is_read': n.is_read,
        'created_at': n.created_at.strftime('%H:%M %d.%m')
    } for n in notifications]
    
    return jsonify({'count': unread_count, 'notifications': data})

@app.route('/api/mark_notifications_read', methods=['POST'])
@login_required
def mark_notifications_read():
    unread = Notification.query.filter_by(user_id=current_user.id, is_read=False).all()
    for n in unread:
        n.is_read = True
    db.session.commit()
    return jsonify({'success': True})


@app.route('/update_stock/<int:item_id>', methods=['POST'])
@login_required
def update_stock(item_id):
    if current_user.role != 'cook': return redirect(url_for('dashboard'))
    
    item = MenuItem.query.get(item_id)
    if item:
        # Поддержка и JSON (для inline-редактирования), и Form data
        if request.is_json:
            item.quantity = int(request.get_json().get('quantity'))
        else:
            item.quantity = int(request.form.get('quantity'))
            
        db.session.commit()
        
        if item.quantity < 5:
             notify_role('cook', f"Внимание! Заканчивается {item.name}. Осталось всего {item.quantity} порций")

        if request.is_json: return jsonify({'success': True})
        
    return redirect(url_for('dashboard'))

@app.route('/create_request', methods=['POST'])
@login_required
def create_request():
    if current_user.role != 'cook': return redirect(url_for('dashboard'))
    req = SupplyRequest(
        product_name=request.form.get('product_name'),
        quantity=int(request.form.get('quantity')),
        priority=request.form.get('priority'),
        total_cost=float(request.form.get('total_cost', 0.0))
    )
    db.session.add(req)
    db.session.commit()
    notify_role('admin', "Повар отправил новую заявку на согласование закупок")
    return redirect(url_for('dashboard'))

@app.route('/auto_request', methods=['POST'])
@login_required
def auto_request():
    if current_user.role != 'cook': return redirect(url_for('dashboard'))
    low_stock = MenuItem.query.filter(MenuItem.quantity < 5).all()
    for item in low_stock:
        req = SupplyRequest(product_name=item.name, quantity=50, priority='Urgent')
        db.session.add(req)
    db.session.commit()
    notify_role('admin', f"Повар сформировал {len(low_stock)} авто-заявок на закупку")
    flash(f'Сформировано {len(low_stock)} заявок.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/approve_request/<int:req_id>', methods=['POST'])
@login_required
def approve_request(req_id):
    if current_user.role != 'admin': return redirect(url_for('dashboard'))
    
    req = SupplyRequest.query.get(req_id)
    if req and req.status == 'Pending':
        try:
            req.status = 'Approved'
            
            # Логика переноса на склад (Продукты)
            product = MenuItem.query.filter_by(name=req.product_name, category='product').first()
            if product:
                product.quantity += req.quantity
            else:
                # Создаем новый продукт (цена 0, так как это сырье)
                new_prod = MenuItem(name=req.product_name, price=0, category='product', quantity=req.quantity, is_active=False)
                db.session.add(new_prod)
                
            db.session.commit()
            flash(f'Заявка на {req.product_name} одобрена и добавлена на склад.', 'success')
            notify_role('cook', f"Администратор одобрил вашу заявку на закупку {req.product_name} ({req.quantity}). Продукты добавлены на склад")
        except Exception as e:
            db.session.rollback()
            flash('Ошибка при обработке заявки.', 'error')
            
    return redirect(url_for('dashboard'))

@app.route('/reject_request/<int:req_id>', methods=['POST'])
@login_required
def reject_request(req_id):
    if current_user.role != 'admin': return redirect(url_for('dashboard'))
    
    req = SupplyRequest.query.get(req_id)
    if req and req.status == 'Pending':
        req.status = 'Rejected'
        db.session.commit()
        flash(f'Заявка на {req.product_name} отклонена.', 'success')
        notify_role('cook', f"Администратор отклонил вашу заявку на закупку {req.product_name}")
    return redirect(url_for('dashboard'))

@app.route('/download_report')
@login_required
def download_report():
    if current_user.role != 'admin': return redirect(url_for('dashboard'))
    
    # Генерация CSV отчета за последние 30 дней
    output = []
    output.append(['Дата', 'Продажи (Руб)', 'Закупки (Руб)', 'Прибыль (Руб)'])
    
    today = date.today()
    for i in range(30):
        d = today - timedelta(days=i)
        d_start = datetime.combine(d, datetime.min.time())
        d_end = d_start + timedelta(days=1)
        
        sales = db.session.query(func.sum(MenuItem.price)).join(Order).filter(Order.timestamp >= d_start, Order.timestamp < d_end, Order.status != 'Issued (Sub)').scalar() or 0
        expenses = db.session.query(func.sum(SupplyRequest.total_cost)).filter(SupplyRequest.created_at >= d_start, SupplyRequest.created_at < d_end, SupplyRequest.status == 'Approved').scalar() or 0
        
        output.append([d.strftime('%Y-%m-%d'), sales, expenses, sales - expenses])
    
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerows(output)
    response = make_response(si.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=monthly_report.csv"
    response.headers["Content-type"] = "text/csv"
    return response

@app.route('/publish_dish/<int:item_id>', methods=['POST'])
@login_required
def publish_dish(item_id):
    if current_user.role != 'cook': return redirect(url_for('dashboard'))
    item = MenuItem.query.get(item_id)
    if item:
        item.is_active = True
        db.session.commit()
        notify_role('student', f"Меню обновлено! Блюдо '{item.name}' теперь доступно для заказа")
        flash(f'Блюдо {item.name} опубликовано в меню!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/reset_db')
def reset_db():
    with app.app_context():
        db.reflect()
        db.drop_all()
        db.create_all()
        # Создаем дефолтного админа
        hashed_pw = generate_password_hash('admin', method='scrypt')
        admin = User(username='admin', password_hash=hashed_pw, role='admin')
        db.session.add(admin)
        db.session.commit()
        flash("База данных полностью очищена. Создан стандартный администратор.", "success")
        return redirect(url_for('login'))

@app.errorhandler(404)
def page_not_found(e):
    return "<h1>404 - Страница не найдена</h1><p>Возможно, обновление еще не завершилось. Попробуйте вернуться на <a href='/'>главную</a>.</p>", 404

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)