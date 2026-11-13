"""Учебный книжный магазин: каталог, заказы, аренда, административный интерфейс, напоминания."""
from __future__ import annotations
import os
import sqlite3
import sys
from datetime import datetime, timedelta, date
from functools import wraps
from pathlib import Path
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash,check_password_hash

BASE=Path(__file__).resolve().parent
app=Flask(__name__)
app.config.update(SECRET_KEY=os.environ.get('SECRET_KEY','dev-change-me'),DATABASE=os.environ.get('DATABASE',str(BASE/'books.sqlite3')))

SCHEMA='''
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT NOT NULL UNIQUE,password TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'user');
CREATE TABLE IF NOT EXISTS books(id INTEGER PRIMARY KEY,title TEXT NOT NULL,author TEXT NOT NULL,category TEXT NOT NULL,year INTEGER NOT NULL,price INTEGER NOT NULL,quantity INTEGER NOT NULL DEFAULT 1,status TEXT NOT NULL DEFAULT 'available');
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,book_id INTEGER NOT NULL,kind TEXT NOT NULL CHECK(kind IN('buy','rent')),amount INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,due_date TEXT,status TEXT NOT NULL DEFAULT 'active');
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,order_id INTEGER NOT NULL,notice_type TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(order_id,notice_type));
'''
SEED=[('Преступление и наказание','Ф. М. Достоевский','Классика',1866,65000,4),('Мастер и Маргарита','М. А. Булгаков','Классика',1967,85000,3),('Пикник на обочине','А. и Б. Стругацкие','Фантастика',1972,72000,3),('Дюна','Фрэнк Герберт','Фантастика',1965,99000,5),('1984','Джордж Оруэлл','Антиутопия',1949,60000,5),('Маленький принц','Антуан де Сент-Экзюпери','Сказка',1943,54000,2)]

def db():
    if 'db' not in g:
        g.db=sqlite3.connect(app.config['DATABASE'])
        g.db.row_factory=sqlite3.Row
        g.db.execute('PRAGMA foreign_keys=ON')
    return g.db

@app.teardown_appcontext
def close_db(err):
    conn=g.pop('db',None)
    if conn:conn.close()

def init_db():
    Path(app.config['DATABASE']).parent.mkdir(parents=True,exist_ok=True)
    db().executescript(SCHEMA)
    if db().execute('SELECT COUNT(*) FROM books').fetchone()[0]==0:
        with db():db().executemany('INSERT INTO books(title,author,category,year,price,quantity) VALUES(?,?,?,?,?,?)',SEED)
    admin_pwd=os.environ.get('ADMIN_PASSWORD')
    if admin_pwd and len(admin_pwd)>=8 and not db().execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
        with db():db().execute('INSERT INTO users(username,password,role) VALUES(?,?,?)',(os.environ.get('ADMIN_USER','admin'),generate_password_hash(admin_pwd),'admin'))

def login_required(fn):
    @wraps(fn)
    def wrap(*a,**kw):
        if not session.get('uid'):flash('Для этой операции войдите в аккаунт.');return redirect(url_for('login'))
        return fn(*a,**kw)
    return wrap

def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrap(*a,**kw):
        if session.get('role')!='admin':abort(403)
        return fn(*a,**kw)
    return wrap

@app.context_processor
def ctx():return {'uid':session.get('uid'),'username':session.get('username'),'role':session.get('role')}
@app.template_filter('rub')
def rub(value):return f'{value/100:,.2f}'.replace(',',' ').replace('.',',')+' ₽'

def book_or_404(bid):
    row=db().execute('SELECT * FROM books WHERE id=?',(bid,)).fetchone()
    if row is None:abort(404)
    return row

@app.get('/')
def index():
    category=request.args.get('category','');author=request.args.get('author','');sort=request.args.get('sort','title')
    params=[];clause=' WHERE 1=1'
    if category:clause+=' AND category=?';params.append(category)
    if author:clause+=' AND author=?';params.append(author)
    order={'title':'title COLLATE NOCASE','year':'year','author':'author COLLATE NOCASE'}.get(sort,'title COLLATE NOCASE')
    books=db().execute('SELECT * FROM books'+clause+' ORDER BY '+order,params).fetchall()
    cats=[r[0] for r in db().execute('SELECT DISTINCT category FROM books ORDER BY category')]
    authors=[r[0] for r in db().execute('SELECT DISTINCT author FROM books ORDER BY author')]
    return render_template('index.html',books=books,cats=cats,authors=authors,cat=category,author=author,sort=sort)

@app.route('/register',methods=['GET','POST'])
def register():
    if request.method=='POST':
        username=request.form.get('username','').strip();password=request.form.get('password','')
        if len(username)<3 or len(password)<8:flash('Имя — от 3 знаков, пароль — от 8.')
        else:
            try:
                with db():db().execute('INSERT INTO users(username,password) VALUES(?,?)',(username,generate_password_hash(password)))
                flash('Регистрация завершена.');return redirect(url_for('login'))
            except sqlite3.IntegrityError:flash('Такой пользователь существует.')
    return render_template('auth.html',mode='Регистрация')

@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        u=db().execute('SELECT * FROM users WHERE username=?',(request.form.get('username',''),)).fetchone()
        if u and check_password_hash(u['password'],request.form.get('password','')):
            session.clear();session.update(uid=u['id'],username=u['username'],role=u['role']);return redirect(url_for('index'))
        flash('Неверные данные.')
    return render_template('auth.html',mode='Вход')

@app.post('/logout')
def logout():session.clear();return redirect(url_for('index'))

@app.get('/book/<int:bid>')
def book(bid):return render_template('book.html',book=book_or_404(bid))

@app.post('/book/<int:bid>/order')
@login_required
def order_book(bid):
    b=book_or_404(bid);kind=request.form.get('kind');days=request.form.get('days','14')
    if b['status']!='available' or b['quantity']<1:flash('Книга недоступна.');return redirect(url_for('book',bid=bid))
    if kind not in ('buy','rent') or kind=='rent' and days not in ('14','30','90'):abort(400)
    due=(date.today()+timedelta(days=int(days))).isoformat() if kind=='rent' else None
    amount=b['price'] if kind=='buy' else round(b['price']*{'14':.20,'30':.35,'90':.65}[days])
    # Учебная модель: заказ фиксируется без реального списания денег.
    with db():
        cursor=db().execute("UPDATE books SET quantity=quantity-1 WHERE id=? AND quantity>0 AND status='available'",(bid,))
        if cursor.rowcount!=1:abort(409)
        db().execute('INSERT INTO orders(user_id,book_id,kind,amount,due_date) VALUES(?,?,?,?,?)',(session['uid'],bid,kind,amount,due))
    flash('Заказ оформлен в учебной системе (без оплаты).');return redirect(url_for('orders'))

@app.get('/orders')
@login_required
def orders():
    rows=db().execute('SELECT o.*,b.title FROM orders o JOIN books b ON b.id=o.book_id WHERE o.user_id=? ORDER BY o.id DESC',(session['uid'],)).fetchall()
    notices=db().execute('SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC',(session['uid'],)).fetchall()
    return render_template('orders.html',rows=rows,notices=notices)

@app.post('/orders/<int:oid>/return')
@login_required
def return_book(oid):
    row=db().execute("SELECT * FROM orders WHERE id=? AND user_id=? AND kind='rent' AND status='active'",(oid,session['uid'])).fetchone()
    if row is None:abort(404)
    with db():
        db().execute("UPDATE orders SET status='returned' WHERE id=?",(oid,))
        db().execute('UPDATE books SET quantity=quantity+1 WHERE id=?',(row['book_id'],))
    flash('Возврат зарегистрирован.');return redirect(url_for('orders'))

@app.get('/admin')
@admin_required
def admin():
    books=db().execute('SELECT * FROM books ORDER BY id').fetchall()
    orders=db().execute('SELECT o.*,u.username,b.title FROM orders o JOIN users u ON u.id=o.user_id JOIN books b ON b.id=o.book_id ORDER BY o.id DESC LIMIT 50').fetchall()
    return render_template('admin.html',books=books,orders=orders)

@app.route('/admin/book/new',methods=['GET','POST'])
@app.route('/admin/book/<int:bid>/edit',methods=['GET','POST'])
@admin_required
def edit_book(bid=None):
    book=book_or_404(bid) if bid else None
    if request.method=='POST':
        try:
            title=request.form['title'].strip();author=request.form['author'].strip();category=request.form['category'].strip()
            year=int(request.form['year']);price=int(round(float(request.form['price'].replace(',','.'))*100));quantity=int(request.form['quantity']);status=request.form.get('status')
            if not title or not author or not category or not 1000<=year<=2100 or price<0 or quantity<0 or status not in ('available','hidden'):raise ValueError
        except (ValueError,KeyError):flash('Проверьте поля книги.');return render_template('edit.html',book=book)
        with db():
            if bid:db().execute('UPDATE books SET title=?,author=?,category=?,year=?,price=?,quantity=?,status=? WHERE id=?',(title,author,category,year,price,quantity,status,bid))
            else:db().execute('INSERT INTO books(title,author,category,year,price,quantity,status) VALUES(?,?,?,?,?,?,?)',(title,author,category,year,price,quantity,status))
        return redirect(url_for('admin'))
    return render_template('edit.html',book=book)

@app.post('/admin/book/<int:bid>/delete')
@admin_required
def delete_book(bid):
    book_or_404(bid)
    if db().execute('SELECT 1 FROM orders WHERE book_id=?',(bid,)).fetchone():flash('Книга участвует в заказах, можно только скрыть её.')
    else:
        with db():db().execute('DELETE FROM books WHERE id=?',(bid,))
    return redirect(url_for('admin'))

def generate_reminders(now=None):
    """Создать уведомления за 2 дня до дедлайна и после него; безопасно для повторных запусков."""
    today=now or date.today();added=0
    rows=db().execute("SELECT o.*,b.title FROM orders o JOIN books b ON b.id=o.book_id WHERE o.kind='rent' AND o.status='active'").fetchall()
    with db():
        for order in rows:
            due=date.fromisoformat(order['due_date']);remaining=(due-today).days
            if remaining>2:continue
            kind='overdue' if remaining<0 else 'upcoming'
            body=f"Срок аренды книги «{order['title']}» {'истёк' if remaining<0 else 'заканчивается'} ({order['due_date']})."
            cursor=db().execute('INSERT OR IGNORE INTO notifications(user_id,order_id,notice_type,body) VALUES(?,?,?,?)',(order['user_id'],order['id'],kind,body))
            added+=cursor.rowcount
    return added

@app.post('/admin/reminders')
@admin_required
def reminders():
    n=generate_reminders();flash(f'Добавлено уведомлений: {n}');return redirect(url_for('admin'))

with app.app_context():init_db()
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='remind':
        with app.app_context():print('Создано уведомлений:',generate_reminders())
    else:app.run(debug=os.environ.get('FLASK_DEBUG')=='1')
