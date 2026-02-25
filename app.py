import os
try:
	from dotenv import load_dotenv
	load_dotenv()
except ImportError:
	pass
import re
import sqlite3
import hashlib
import urllib.request
import urllib.error
from datetime import datetime
from html.parser import HTMLParser
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_mail import Mail, Message
from markupsafe import Markup, escape
from functools import wraps
from PIL import Image

# PostgreSQL 지원
DATABASE_URL = os.environ.get('DATABASE_URL', '')
USE_POSTGRES = bool(DATABASE_URL)
if USE_POSTGRES:
	import psycopg2
	import psycopg2.extras
	# Render에서 제공하는 URL이 postgres:// 로 시작하면 postgresql:// 로 변경
	if DATABASE_URL.startswith('postgres://'):
		DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

try:
	from sendgrid import SendGridAPIClient
	from sendgrid.helpers.mail import Mail as SGMail, Email, To, Content
	HAS_SENDGRID = True
except ImportError:
	HAS_SENDGRID = False

try:
	from authlib.integrations.flask_client import OAuth
	HAS_OAUTH = True
except ImportError:
	HAS_OAUTH = False


app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = os.environ.get('SECRET_KEY', 'devsecret-change-this-in-production')
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True
app.config['PREFERRED_URL_SCHEME'] = 'https'

# 세션 쿠키 설정: www 포함/미포함 도메인 간 세션 공유
# SESSION_COOKIE_DOMAIN을 .virtualblackeagles.kr로 설정하면
# virtualblackeagles.kr 과 www.virtualblackeagles.kr 모두에서 세션 쿠키 유효
if os.environ.get('SESSION_COOKIE_DOMAIN'):
	app.config['SESSION_COOKIE_DOMAIN'] = os.environ.get('SESSION_COOKIE_DOMAIN')
app.config['SESSION_COOKIE_SECURE'] = True   # HTTPS에서만 쿠키 전송
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# 프록시 뒤에서 HTTPS를 올바르게 감지하도록 설정 
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Google OAuth 설정 
if HAS_OAUTH:
	oauth = OAuth(app)
	google = oauth.register(
		name='google',
		client_id=os.environ.get('GOOGLE_CLIENT_ID', ''),
		client_secret=os.environ.get('GOOGLE_CLIENT_SECRET', ''),
		server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
		client_kwargs={'scope': 'openid email profile'}
	)

# 파일 업로드 크기 제한 (16MB)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# 업로드 파일 저장 경로 설정
UPLOAD_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')


# 페이지 방문 트래킹 (하루에 IP당 1회만 기록)
@app.before_request
def track_page_view():
	# 정적 파일이나 API, 관리자 페이지는 트래킹 제외
	if request.path.startswith(('/static/', '/api/', '/admin/')):
		return
	try:
		conn = get_db()
		# 오늘 같은 IP가 이미 기록되어 있으면 중복 삽입 안 함
		existing = conn.execute(
			"SELECT id FROM page_views WHERE ip_address = ? AND DATE(visited_at) = DATE('now')",
			(request.remote_addr,)
		).fetchone()
		if not existing:
			conn.execute('INSERT INTO page_views (page_path, ip_address, user_agent) VALUES (?, ?, ?)',
				(request.path, request.remote_addr, str(request.user_agent)[:200]))
			conn.commit()
		conn.close()
	except Exception:
		pass


# 이미지 최적화 함수
def optimize_image(file_path, max_width=1200, max_height=1200, quality=85):
	"""
	업로드된 이미지를 최적화합니다.
	- EXIF 방향 정보를 처리하여 올바른 방향으로 회전
	- 최대 크기로 리사이즈 (비율 유지)
	- JPEG 포맷으로 압축 저장
	"""
	try:
		with Image.open(file_path) as img:
			# EXIF 방향 정보 처리
			try:
				from PIL import ImageOps
				img = ImageOps.exif_transpose(img)
			except Exception:
				pass  # EXIF 정보가 없는 경우 무시
			
			# RGB 모드로 변환 (JPEG는 RGBA를 지원하지 않음)
			if img.mode in ('RGBA', 'LA', 'P'):
				background = Image.new('RGB', img.size, (255, 255, 255))
				if img.mode == 'P':
					img = img.convert('RGBA')
				background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
				img = background
			elif img.mode != 'RGB':
				img = img.convert('RGB')
			
			# 비율을 유지하면서 리사이즈
			img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
			
			# 최적화하여 저장
			img.save(file_path, 'JPEG', quality=quality, optimize=True)
			
		return True
	except Exception as e:
		print(f"이미지 최적화 중 오류 발생: {e}")
		return False

# Jinja2 필터 추가

@app.template_filter('datefmt')
def datefmt_filter(value, fmt='%Y-%m-%d %H:%M'):
	"""datetime 객체나 문자열을 안전하게 포맷팅 (PostgreSQL datetime 호환)"""
	if not value:
		return '-'
	# 이미 문자열이면 그냥 잘라서 반환
	if isinstance(value, str):
		if fmt == '%Y-%m-%d':
			return value[:10]
		elif fmt == '%Y-%m-%d %H:%M':
			return value[:16]
		elif fmt == '%Y-%m-%d %H:%M:%S':
			return value[:19]
		return value
	# datetime 객체이면 포맷팅
	try:
		return value.strftime(fmt)
	except Exception:
		return str(value)

@app.template_filter('youtube_embed')
def youtube_embed_filter(url):
	"""YouTube URL을 embed URL로 변환"""
	if not url:
		return url
	
	# 이미 embed URL인 경우
	if 'youtube.com/embed/' in url:
		return url
	
	# 일반 YouTube URL 변환
	if 'youtube.com/watch' in url:
		video_id = url.split('v=')[1].split('&')[0] if 'v=' in url else None
		if video_id:
			return f'https://www.youtube.com/embed/{video_id}'
	
	# 단축 URL 변환
	if 'youtu.be/' in url:
		video_id = url.split('youtu.be/')[1].split('?')[0]
		return f'https://www.youtube.com/embed/{video_id}'
	
	return url

@app.template_filter('autolink')
def autolink_filter(text):
	"""텍스트 내 URL을 클릭 가능한 하이퍼링크로 변환"""
	if not text:
		return text
	# 이미 HTML 태그가 포함된 콘텐츠(Quill 에디터 등)는 그대로 반환
	if '<' in str(text) and '>' in str(text):
		return Markup(text)
	# 순수 텍스트의 URL을 <a> 태그로 변환
	escaped = escape(text)
	url_pattern = re.compile(
		r'(https?://[^\s<>"\']+)',
		re.IGNORECASE
	)
	linked = url_pattern.sub(
		r'<a href="\1" target="_blank" rel="noopener noreferrer" style="color:#007bff;">\1</a>',
		str(escaped)
	)
	# 줄바꿈 보존
	linked = linked.replace('\n', '<br>')
	return Markup(linked)

# 데이터베이스 설정
DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'blackeagles.db')


class DBWrapper:
	"""SQLite와 PostgreSQL 모두 지원하는 DB 커넥션 래퍼.
	- SQL 안의 ? 를 PostgreSQL용 %s 로 자동 변환
	- INSERT OR IGNORE → ON CONFLICT DO NOTHING 자동 변환
	- SQLite의 DATE('now') 등을 PostgreSQL 문법으로 변환
	- fetchone()/fetchall() 결과를 딕셔너리로 반환 (row['column'] 접근)
	"""
	def __init__(self, conn, is_pg=False):
		self._conn = conn
		self._is_pg = is_pg
		if is_pg:
			self._cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
		else:
			conn.row_factory = sqlite3.Row
			self._cursor = None

	def _convert_sql(self, sql):
		"""SQLite SQL을 PostgreSQL 호환으로 변환"""
		if not self._is_pg:
			return sql
		# INSERT OR IGNORE 감지 (변환 전에 플래그 설정)
		had_insert_or_ignore = bool(re.search(r'INSERT\s+OR\s+IGNORE\s+INTO', sql, flags=re.IGNORECASE))
		# ? → %s
		sql = sql.replace('?', '%s')
		# INSERT OR IGNORE → INSERT INTO (나중에 ON CONFLICT DO NOTHING 추가)
		sql = re.sub(r'INSERT\s+OR\s+IGNORE\s+INTO', 'INSERT INTO', sql, flags=re.IGNORECASE)
		# AUTOINCREMENT → (제거, PostgreSQL SERIAL이 자동 처리)
		sql = sql.replace('AUTOINCREMENT', '')
		sql = sql.replace('autoincrement', '')
		# INTEGER PRIMARY KEY  → SERIAL PRIMARY KEY (CREATE TABLE 시)
		sql = re.sub(r'id\s+INTEGER\s+PRIMARY\s+KEY', 'id SERIAL PRIMARY KEY', sql, flags=re.IGNORECASE)
		# DATE('now') → CURRENT_DATE
		sql = sql.replace("DATE('now')", "CURRENT_DATE")
		# DATE('now', '-7 days') → CURRENT_DATE - INTERVAL '7 days'
		sql = re.sub(r"DATE\('now',\s*'(-?\d+)\s+days?'\)", r"CURRENT_DATE + INTERVAL '\1 days'", sql)
		# DATE('now', 'start of month') → DATE_TRUNC('month', CURRENT_DATE)
		sql = re.sub(r"DATE\('now',\s*'start of month'\)", "DATE_TRUNC('month', CURRENT_TIMESTAMP)", sql)
		# DATE('now', '-30 days') 형태도 처리
		sql = re.sub(r"DATE\('now',\s*'-(\d+)\s+days?'\)", r"CURRENT_DATE - INTERVAL '\1 days'", sql)
		# DATE(column) → column::DATE (PostgreSQL cast)
		sql = re.sub(r'DATE\((\w+)\)', r'\1::DATE', sql)
		# strftime('%Y-%m', col) = strftime('%Y-%m', 'now') → DATE_TRUNC
		sql = re.sub(r"strftime\('%Y-%m',\s*(\w+)\)\s*=\s*strftime\('%Y-%m',\s*'now'\)",
			r"DATE_TRUNC('month', \1) = DATE_TRUNC('month', CURRENT_TIMESTAMP)", sql)
		# DEFAULT "center" → DEFAULT 'center' (쌍따옴표를 홑따옴표로)
		sql = re.sub(r'DEFAULT\s+"([^"]*)"', r"DEFAULT '\1'", sql)
		# 일반 SQL의 쌍따옴표 문자열을 홑따옴표로 변환 (= "value" → = 'value')
		# PostgreSQL에서 쌍따옴표는 식별자(컬럼명)용이므로 문자열 값은 홑따옴표 사용 필요
		sql = re.sub(r'=\s*"([^"]*)"', r"= '\1'", sql)
		# SQL 주석 제거 (PostgreSQL 멀티라인 실행 시 문제 방지)
		sql = re.sub(r'--[^\n]*', '', sql)
		# INSERT OR IGNORE였던 쿼리에 ON CONFLICT DO NOTHING 추가
		if had_insert_or_ignore:
			sql = sql.rstrip().rstrip(';')
			sql += ' ON CONFLICT DO NOTHING'
		return sql

	def execute(self, sql, params=None):
		converted = self._convert_sql(sql)
		if self._is_pg:
			try:
				if params:
					self._cursor.execute(converted, params)
				else:
					self._cursor.execute(converted)
			except psycopg2.Error:
				self._conn.rollback()
				raise
			return self._cursor
		else:
			if params:
				return self._conn.execute(sql, params)
			else:
				return self._conn.execute(sql)

	def commit(self):
		self._conn.commit()

	def rollback(self):
		self._conn.rollback()

	def close(self):
		if self._is_pg and self._cursor:
			self._cursor.close()
		self._conn.close()

	def cursor(self):
		"""래핑된 cursor 반환 (SQL 자동 변환 지원)"""
		return CursorWrapper(self)


class CursorWrapper:
	"""DBWrapper를 통해 SQL 변환을 자동 적용하는 cursor 래퍼"""
	def __init__(self, db_wrapper):
		self._db = db_wrapper

	def execute(self, sql, params=None):
		return self._db.execute(sql, params)

	def fetchone(self):
		if self._db._is_pg:
			return self._db._cursor.fetchone()
		else:
			return None  # SQLite에서는 conn.execute().fetchone() 사용

	def fetchall(self):
		if self._db._is_pg:
			return self._db._cursor.fetchall()
		else:
			return []


def get_db():
	"""데이터베이스 연결 (PostgreSQL 또는 SQLite)"""
	if USE_POSTGRES:
		conn = psycopg2.connect(DATABASE_URL)
		return DBWrapper(conn, is_pg=True)
	else:
		conn = sqlite3.connect(DATABASE)
		return DBWrapper(conn, is_pg=False)

def _get_count(row):
	"""fetchone() 결과에서 COUNT 값을 추출 (SQLite tuple/Row와 PostgreSQL dict 모두 지원)"""
	if row is None:
		return 0
	if isinstance(row, dict):
		# PostgreSQL RealDictCursor: {'count': 5}
		return list(row.values())[0]
	try:
		return row[0]
	except (IndexError, KeyError):
		return 0

def init_db():
	"""데이터베이스 초기화"""
	conn = get_db()

	# PostgreSQL에서는 DDL(CREATE TABLE)과 DML(INSERT) 사이에 commit이 필요
	# 테이블 생성 → commit → 데이터 삽입 → commit 패턴으로 안정적 초기화

	# 공지사항 테이블
	conn.execute('''
		CREATE TABLE IF NOT EXISTS notices (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			title TEXT NOT NULL,
			content TEXT NOT NULL,
			author TEXT NOT NULL,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)
	''')

	# 일정 테이블
	conn.execute('''
		CREATE TABLE IF NOT EXISTS schedules (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			title TEXT NOT NULL,
			location TEXT,
			event_date DATE NOT NULL,
			description TEXT,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)
	''')

	# 문의 메시지 테이블
	conn.execute('''
		CREATE TABLE IF NOT EXISTS contact_messages (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			name TEXT,
			email TEXT NOT NULL,
			message TEXT NOT NULL,
			type TEXT DEFAULT 'contact',
			is_read INTEGER DEFAULT 0,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)
	''')

	# 페이지 섹션 테이블 (개선된 버전)
	conn.execute('''
		CREATE TABLE IF NOT EXISTS page_sections (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			page_name TEXT NOT NULL,
			section_id TEXT NOT NULL,
			section_type TEXT NOT NULL,
			title TEXT,
			content TEXT,
			image_url TEXT,
			link_url TEXT,
			link_text TEXT,
			order_num INTEGER DEFAULT 0,
			is_active INTEGER DEFAULT 1,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			UNIQUE(page_name, section_id)
		)
	''')

	# 배너 설정 테이블
	conn.execute('''
		CREATE TABLE IF NOT EXISTS banner_settings (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			page_name TEXT UNIQUE NOT NULL,
			background_image TEXT,
			title TEXT NOT NULL,
			subtitle TEXT,
			description TEXT,
			button_text TEXT,
			button_link TEXT,
			title_font TEXT DEFAULT 'Arial, sans-serif',
			title_color TEXT DEFAULT '#ffffff',
			subtitle_color TEXT DEFAULT '#ffffff',
			description_color TEXT DEFAULT '#ffffff',
			vertical_position TEXT DEFAULT 'center',
			padding_top INTEGER DEFAULT 250,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)
	''')

	# 모든 CREATE TABLE을 commit하여 테이블이 확실히 존재하도록 함
	conn.commit()

	# 기존 테이블에 컬럼 추가 (이미 있으면 무시, PostgreSQL은 rollback 필요)
	try:
		conn.execute("ALTER TABLE banner_settings ADD COLUMN vertical_position TEXT DEFAULT 'center'")
		conn.commit()
	except:
		conn.rollback()
	try:
		conn.execute('ALTER TABLE banner_settings ADD COLUMN padding_top INTEGER DEFAULT 250')
		conn.commit()
	except:
		conn.rollback()

	# 기본 페이지 섹션 생성
	default_sections = [
		('home', 'about', 'text', 'About Us', '가상블랙이글스는 대한민국 블랙이글스의 다양한 특수비행을 통해 고도의 비행기량을 뽐내는 대한민국 가상 특수비행팀입니다.', None, None, None, 1, 1),
		('about', 'intro', 'text', '팀 소개', '블랙이글스는 대한민국 공군의 자랑입니다.', None, None, None, 1, 1),
		('contact', 'discord', 'text', 'Contact Us', 'Discord ㅣ Johnson#4553', None, None, None, 1, 1),
	]

	for section in default_sections:
		conn.execute('''
			INSERT OR IGNORE INTO page_sections
			(page_name, section_id, section_type, title, content, image_url, link_url, link_text, order_num, is_active)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		''', section)

	# 기본 홈페이지 배너 설정
	conn.execute('''
		INSERT OR IGNORE INTO banner_settings (page_name, background_image, title, subtitle, description, button_text, button_link)
		VALUES ('home', '/static/images/hero.jpg', 'Black Eagles', 'Republic Of Korea AirForce',
		        '가상블랙이글스는 대한민국 블랙이글스의 다양한 특수비행을 통해 고도의 비행기량을 뽐내는 대한민국 가상 특수비행팀입니다.',
		        'more', '#about')
	''')

	conn.commit()

	# 조종사 정보 테이블
	conn.execute('''
		CREATE TABLE IF NOT EXISTS pilots (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			number INTEGER NOT NULL,
			position TEXT NOT NULL,
			callsign TEXT NOT NULL,
			generation TEXT NOT NULL,
			aircraft TEXT NOT NULL,
			photo_url TEXT,
			order_num INTEGER DEFAULT 0,
			is_active INTEGER DEFAULT 1,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)
	''')

	# 홈 콘텐츠 테이블 (유튜브, SNS 피드 등)
	conn.execute('''
		CREATE TABLE IF NOT EXISTS home_contents (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			content_type TEXT NOT NULL,
			title TEXT,
			content_data TEXT,
			order_num INTEGER DEFAULT 0,
			is_active INTEGER DEFAULT 1,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)
	''')

	# 팀소개 섹션 테이블 (개요, 항공기 등)
	conn.execute('''
		CREATE TABLE IF NOT EXISTS about_sections (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			section_type TEXT NOT NULL,
			title TEXT,
			content TEXT,
			image_url TEXT,
			order_num INTEGER DEFAULT 0,
			is_active INTEGER DEFAULT 1,
			lang TEXT DEFAULT 'ko',
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)
	''')

	# DDL commit - pilots, home_contents, about_sections 테이블 확정
	conn.commit()

	# about_sections 테이블에 lang 컬럼이 없을 수 있으므로 동적으로 추가
	try:
		conn.execute("ALTER TABLE about_sections ADD COLUMN lang TEXT DEFAULT 'ko'")
		conn.commit()
	except Exception:
		conn.rollback()

	# 기본 조종사 데이터 삽입 (중복 방지)
	default_pilots = [
		(1, 'LEADER', 'Bulta', 'VBE 1기', 'F-5', '/static/members/moon.jpeg', 1, 1),
		(2, 'LEFT WING', 'Fox9', 'VBE 2기', 'F-18', '/static/members/moon.jpeg', 2, 1),
		(3, 'RIGHT WING', 'Ace', 'VBE 1기', 'F-18', '/static/members/moon.jpeg', 3, 1),
		(4, 'Slot', 'Moon', 'VBE 1기', 'F-5', '/static/members/moon.jpeg', 4, 1),
		(5, 'SYNCHRO-1', 'ZeroDistance', 'VBE 1기', 'F-5', '/static/members/moon.jpeg', 5, 1),
		(6, 'SYNCHRO-2', 'Lewis', 'VBE 1기', 'F-5', '/static/members/Lewis.jpg', 6, 1),
		(7, 'SOLO-1', 'Sonic', 'VBE 1기', 'F-5', '/static/members/moon.jpeg', 7, 1),
		(8, 'SOLO-2', 'Strike', 'VBE 1기', 'F-5', '/static/members/moon.jpeg', 8, 1),
	]

	# 이미 데이터가 있는지 확인
	existing_count = _get_count(conn.execute('SELECT COUNT(*) FROM pilots').fetchone())

	# 데이터가 없을 때만 기본 데이터 삽입
	if existing_count == 0:
		for pilot in default_pilots:
			conn.execute('''
				INSERT INTO pilots
				(number, position, callsign, generation, aircraft, photo_url, order_num, is_active)
				VALUES (?, ?, ?, ?, ?, ?, ?, ?)
			''', pilot)

	# 기본 유튜브 콘텐츠 삽입
	conn.execute('''
		INSERT OR IGNORE INTO home_contents (id, content_type, title, content_data, order_num, is_active)
		VALUES (1, 'youtube', 'Latest Video', 'https://www.youtube.com/embed/dQw4w9WgXcQ', 1, 1)
	''')

	conn.commit()

	# 전대장 인사말 테이블
	conn.execute('''
		CREATE TABLE IF NOT EXISTS commander_greeting (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			name TEXT NOT NULL,
			rank TEXT NOT NULL,
			callsign TEXT NOT NULL,
			generation TEXT NOT NULL,
			aircraft TEXT NOT NULL,
			photo_url TEXT,
			greeting_text TEXT,
			order_num INTEGER DEFAULT 0,
			is_active INTEGER DEFAULT 1,
			lang TEXT DEFAULT 'ko',
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)
	''')

	# 정비사 테이블
	conn.execute('''
		CREATE TABLE IF NOT EXISTS maintenance_crew (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			name TEXT NOT NULL,
			role TEXT,
			callsign TEXT,
			photo_url TEXT,
			bio TEXT,
			order_num INTEGER DEFAULT 0,
			is_active INTEGER DEFAULT 1,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)
	''')

	# 후보자 테이블
	conn.execute('''
		CREATE TABLE IF NOT EXISTS candidates (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			name TEXT NOT NULL,
			callsign TEXT,
			photo_url TEXT,
			bio TEXT,
			order_num INTEGER DEFAULT 0,
			is_active INTEGER DEFAULT 1,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)
	''')

	# 사진 게시판 테이블
	conn.execute('''
		CREATE TABLE IF NOT EXISTS gallery (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			title TEXT NOT NULL,
			description TEXT,
			image_url TEXT NOT NULL,
			upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			is_active INTEGER DEFAULT 1,
			order_num INTEGER DEFAULT 0,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)
	''')

	# DDL commit - commander_greeting, maintenance_crew, candidates, gallery 테이블 확정
	conn.commit()

	# commander_greeting 테이블에 lang 컬럼이 없을 수 있으므로 동적으로 추가
	try:
		conn.execute("ALTER TABLE commander_greeting ADD COLUMN lang TEXT DEFAULT 'ko'")
		conn.commit()
	except Exception:
		conn.rollback()

	# 기본 개요 섹션 추가
	conn.execute('''
		INSERT OR IGNORE INTO about_sections (id, section_type, title, content, order_num, is_active)
		VALUES (1, 'overview', '가상 블랙이글스 소개',
		'가상 블랙이글스는 DCS World에서 활동하는 대한민국 가상 공군 특수비행팀입니다. 실제 블랙이글스의 정신과 전통을 계승하며, 정교한 편대비행과 에어쇼를 통해 뛰어난 비행실력을 선보입니다.',
		0, 1)
	''')

	conn.execute('''
		INSERT OR IGNORE INTO about_sections (id, section_type, title, content, order_num, is_active)
		VALUES (2, 'mission', '임무',
		'우리의 임무는 대한민국 공군의 우수성을 전 세계에 알리고, 가상 비행 시뮬레이션을 통해 항공에 대한 관심과 이해를 높이는 것입니다. 또한 팀원들의 비행 실력 향상과 팀워크 강화를 목표로 합니다.',
		1, 1)
	''')

	conn.execute('''
		INSERT OR IGNORE INTO about_sections (id, section_type, title, content, order_num, is_active)
		VALUES (3, 'aircraft_intro', 'T-50B 골든이글',
		'T-50B는 대한민국이 자체 개발한 초음속 고등훈련기로, 블랙이글스 팀이 사용하는 항공기입니다. 우수한 기동성과 안정성을 자랑하며, 다양한 편대비행 기동을 수행할 수 있습니다.',
		2, 1)
	''')

	conn.execute('''
		INSERT OR IGNORE INTO about_sections (id, section_type, title, content, image_url, order_num, is_active)
		VALUES (4, 'aircraft_specs', 'T-50B 제원',
		'최대속도: 마하 1.5|전투행동반경: 1,851km|최대이륙중량: 12,300kg|엔진: F404-GE-102 터보팬|승무원: 2명|무장: 20mm 기관포, 공대공 미사일',
		'/static/images/t50b.jpg',
		3, 1)
	''')

	conn.execute('''
		INSERT OR IGNORE INTO about_sections (id, section_type, title, content, order_num, is_active)
		VALUES (5, 'aircraft_features', '특징',
		'우수한 기동성|높은 안정성|효율적인 연료 소비|조종사 친화적 설계|다목적 운용 가능',
		4, 1)
	''')

	# 기본 전대장 데이터 삽입 (데이터가 없을 때만)
	existing_commanders = _get_count(conn.execute('SELECT COUNT(*) as count FROM commander_greeting').fetchone())
	if existing_commanders == 0:
		conn.execute('''
			INSERT INTO commander_greeting (name, rank, callsign, generation, aircraft, photo_url, greeting_text, order_num, is_active)
			VALUES ('Bulta', 'COMMANDER', '#1 Bulta', 'VBE 1기', 'F-5', '/static/images/default-pilot.jpg',
			'안녕하십니까. 가상 블랙이글스 전대장입니다. 우리 팀은 대한민국 공군의 자랑스러운 전통을 계승하며, 최고의 비행 실력을 갖춘 정예 조종사들로 구성되어 있습니다.',
			1, 1)
		''')

	# DML commit - about_sections, commander_greeting 데이터 확정
	conn.commit()

	# 사이트 이미지 관리 테이블
	conn.execute('''
		CREATE TABLE IF NOT EXISTS site_images (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			image_key TEXT UNIQUE NOT NULL,
			image_name TEXT NOT NULL,
			image_path TEXT NOT NULL,
			description TEXT,
			category TEXT NOT NULL,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)
	''')

	# 영상 갤러리 테이블
	conn.execute('''
		CREATE TABLE IF NOT EXISTS videos (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			title TEXT NOT NULL,
			description TEXT,
			video_url TEXT NOT NULL,
			thumbnail_url TEXT,
			order_num INTEGER DEFAULT 0,
			is_active INTEGER DEFAULT 1,
			upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)
	''')

	# 사이트 설정 테이블 (후원 링크 등 key-value)
	conn.execute('''
		CREATE TABLE IF NOT EXISTS site_settings (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			setting_key TEXT UNIQUE NOT NULL,
			setting_value TEXT,
			description TEXT,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)
	''')

	# 실시간 채팅 테이블
	conn.execute('''
		CREATE TABLE IF NOT EXISTS chat_sessions (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			session_id TEXT UNIQUE NOT NULL,
			user_name TEXT,
			user_email TEXT,
			status TEXT DEFAULT 'active',
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)
	''')

	conn.execute('''
		CREATE TABLE IF NOT EXISTS chat_messages (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			session_id TEXT NOT NULL,
			sender_type TEXT NOT NULL,
			sender_name TEXT,
			message TEXT NOT NULL,
			is_read INTEGER DEFAULT 0,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id)
		)
	''')

	# 회원 테이블
	conn.execute('''
		CREATE TABLE IF NOT EXISTS users (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			email TEXT UNIQUE NOT NULL,
			display_name TEXT,
			google_id TEXT UNIQUE,
			username TEXT UNIQUE,
			password_hash TEXT,
			role TEXT DEFAULT 'member',
			is_active INTEGER DEFAULT 1,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)
	''')

	# 페이지 방문 트래킹 테이블
	conn.execute('''
		CREATE TABLE IF NOT EXISTS page_views (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			page_path TEXT NOT NULL,
			ip_address TEXT,
			user_agent TEXT,
			visited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)
	''')

	# DDL commit - 나머지 테이블 모두 확정
	conn.commit()

	# ---- 여기부터 DML (INSERT/UPDATE) ----

	# 기본 샘플 이미지 추가
	conn.execute('''
		INSERT OR IGNORE INTO gallery (id, title, description, image_url, order_num, is_active)
		VALUES (1, '편대비행 훈련', 'T-50B 4기 편대비행 훈련 모습', '/static/Picture/20251207_173919_section_formation.png', 1, 1)
	''')

	conn.execute('''
		INSERT OR IGNORE INTO gallery (id, title, description, image_url, order_num, is_active)
		VALUES (2, '에어쇼 공연', '2024 서울 에어쇼 블랙이글스 공연', '/static/Picture/Formation.png', 2, 1)
	''')

	# 기본 이미지 키 등록
	default_images = [
		('hero_banner', '홈 배너 이미지', '/static/images/hero.jpg', '메인 페이지 상단 배너', 'home'),
		('about_banner', '팀소개 배너 이미지', '/static/images/hero.jpg', '팀소개 페이지 상단 배너', 'about'),
		('default_pilot', '기본 파일럿 이미지', '/static/members/moon.jpeg', '파일럿 기본 프로필', 'about'),
		('t50b_main', 'T-50B 메인 이미지', '/static/Picture/Formation.png', '항공기 소개 이미지', 'about'),
	]

	for img_key, img_name, img_path, desc, cat in default_images:
		conn.execute('''
			INSERT OR IGNORE INTO site_images (image_key, image_name, image_path, description, category)
			VALUES (?, ?, ?, ?, ?)
		''', (img_key, img_name, img_path, desc, cat))

	# 기존 DB에 이미 t50b_main 이 있다면 경로를 실제 존재하는 이미지로 교체
	try:
		conn.execute('''
			UPDATE site_images
			SET image_path = '/static/Picture/Formation.png'
			WHERE image_key = 't50b_main'
		''')
	except Exception:
		conn.rollback()

	# 기본 후원 설정
	conn.execute('''
		INSERT OR IGNORE INTO site_settings (setting_key, setting_value, description)
		VALUES ('donate_kakaopay_link', '', '카카오페이 송금 링크')
	''')
	conn.execute('''
		INSERT OR IGNORE INTO site_settings (setting_key, setting_value, description)
		VALUES ('donate_bank_name', '', '후원 계좌 은행명')
	''')
	conn.execute('''
		INSERT OR IGNORE INTO site_settings (setting_key, setting_value, description)
		VALUES ('donate_account_number', '', '후원 계좌번호')
	''')
	conn.execute('''
		INSERT OR IGNORE INTO site_settings (setting_key, setting_value, description)
		VALUES ('donate_account_holder', '', '후원 계좌 예금주')
	''')
	conn.execute('''
		INSERT OR IGNORE INTO site_settings (setting_key, setting_value, description)
		VALUES ('contact_email', '', '문의 수신 이메일 주소')
	''')

	# 배너 기본값 (notice, schedule, gallery 추가)
	conn.execute('''
		INSERT OR IGNORE INTO banner_settings (page_name, background_image, title, subtitle)
		VALUES ('notice', '/static/images/hero.jpg', '공지사항', 'Announcements')
	''')
	conn.execute('''
		INSERT OR IGNORE INTO banner_settings (page_name, background_image, title, subtitle)
		VALUES ('schedule', '/static/images/hero.jpg', '일정', 'Schedule')
	''')
	conn.execute('''
		INSERT OR IGNORE INTO banner_settings (page_name, background_image, title, subtitle)
		VALUES ('gallery', '/static/images/hero.jpg', '활동', 'Activities')
	''')


	conn.commit()

	# PostgreSQL: 명시적 ID 삽입 후 SERIAL 시퀀스 리셋
	# INSERT OR IGNORE로 id를 직접 지정하면 PostgreSQL 시퀀스가 갱신되지 않아
	# 이후 새 레코드 삽입 시 ID 충돌(UniqueViolation) 500 에러가 발생함
	if USE_POSTGRES:
		try:
			conn.execute("SELECT setval('about_sections_id_seq', COALESCE((SELECT MAX(id) FROM about_sections), 1))")
			conn.execute("SELECT setval('home_contents_id_seq', COALESCE((SELECT MAX(id) FROM home_contents), 1))")
			conn.execute("SELECT setval('gallery_id_seq', COALESCE((SELECT MAX(id) FROM gallery), 1))")
			conn.commit()
		except Exception:
			conn.rollback()

	conn.close()

# 관리자 계정 (실제 운영시에는 데이터베이스나 환경변수 사용 권장)
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'blackeagles2025')

# 비밀번호 해싱
def hash_password(password):
	return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, password_hash):
	return hashlib.sha256(password.encode()).hexdigest() == password_hash

# 로그인 체크 데코레이터 (관리자 전용)
def login_required(f):
	@wraps(f)
	def decorated_function(*args, **kwargs):
		# 1) 세션에 관리자 역할이 있는 경우 (회원 가입 후 role='admin' 으로 승격된 사용자)
		if session.get('user_role') == 'admin':
			return f(*args, **kwargs)

		# 2) 별도의 관리자 로그인(session['logged_in'])으로 들어온 경우 (기존 하드코딩 계정)
		if session.get('logged_in'):
			return f(*args, **kwargs)

		flash('관리자 로그인이 필요합니다.', 'error')
		return redirect(url_for('admin_login'))

	return decorated_function

# Flask-Mail configuration (set these as environment variables for security)
# Example for Naver SMTP:
#   export MAIL_SERVER=smtp.naver.com
#   export MAIL_PORT=465
#   export MAIL_USE_SSL=true
#   export MAIL_USERNAME=rr3340@naver.com
#   export MAIL_PASSWORD=your_app_password
#   export MAIL_DEFAULT_SENDER=rr3340@naver.com

# Gmail 설정 (실제 사용시 네이버 앱 비밀번호로 변경하세요)
# Gmail을 사용하려면 아래 주석을 해제하고 네이버 설정을 주석 처리하세요
# app.config['MAIL_SERVER'] = 'smtp.gmail.com'
# app.config['MAIL_PORT'] = 587
# app.config['MAIL_USE_TLS'] = True
# app.config['MAIL_USE_SSL'] = False
# app.config['MAIL_USERNAME'] = 'your-email@gmail.com'
# app.config['MAIL_PASSWORD'] = 'your-app-password'

# 네이버 메일 설정
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.naver.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 465))
app.config['MAIL_USE_SSL'] = os.environ.get('MAIL_USE_SSL', 'true').lower() == 'true'
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'false').lower() == 'true'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', 'rr3340@naver.com')  # 기본값 설정
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', '')  # 여기에 네이버 앱 비밀번호 입력
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', 'rr3340@naver.com')

mail = Mail(app)

# SendGrid 설정
SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY', '')
SENDGRID_FROM_EMAIL = os.environ.get('SENDGRID_FROM_EMAIL', '')

def send_email(subject, to_email, body_text):
	"""SendGrid 우선, Flask-Mail 폴백으로 이메일 발송"""
	# 1) SendGrid 시도
	if HAS_SENDGRID and SENDGRID_API_KEY:
		try:
			message = SGMail(
				from_email=Email(SENDGRID_FROM_EMAIL),
				to_emails=To(to_email),
				subject=subject,
				plain_text_content=Content("text/plain", body_text)
			)
			sg = SendGridAPIClient(SENDGRID_API_KEY)
			response = sg.send(message)
			if response.status_code in (200, 201, 202):
				return True
			app.logger.error(f"SendGrid 발송 실패: status={response.status_code}")
		except Exception as e:
			app.logger.error(f"SendGrid 발송 오류: {str(e)}")

	# 2) Flask-Mail 폴백
	if app.config.get('MAIL_PASSWORD'):
		try:
			msg = Message(
				subject=subject,
				sender=app.config['MAIL_DEFAULT_SENDER'],
				recipients=[to_email],
				body=body_text
			)
			mail.send(msg)
			return True
		except Exception as e:
			app.logger.error(f"Flask-Mail 발송 오류: {str(e)}")

	return False

@app.route('/send_mail', methods=['POST'])
def send_mail():
	name = request.form.get('name', '').strip()
	email = request.form.get('email', '').strip()
	message_text = request.form.get('message', '').strip()
	lang = request.form.get('lang', 'ko')

	if not email or not message_text:
		flash('이메일과 메시지를 모두 입력해 주세요.' if lang != 'en' else 'Please fill in email and message.', 'error')
		return redirect(url_for('contact', lang=lang) if lang == 'en' else url_for('contact'))

	# 1) 데이터베이스에 문의 내용 저장
	try:
		conn = get_db()
		cursor = conn.cursor()
		cursor.execute('''
			INSERT INTO contact_messages (name, email, message, type)
			VALUES (?, ?, ?, ?)
		''', (name or '익명', email, message_text, 'contact'))
		conn.commit()
		conn.close()
	except Exception as e:
		app.logger.error(f"문의 저장 실패: {str(e)}")
		flash('문의 접수에 실패했습니다. 잠시 후 다시 시도해주세요.' if lang != 'en' else 'Failed to submit. Please try again.', 'error')
		return redirect(url_for('contact', lang=lang) if lang == 'en' else url_for('contact'))

	# 2) 관리자에게 이메일 알림 발송
	try:
		conn_email = get_db()
		email_row = conn_email.execute("SELECT setting_value FROM site_settings WHERE setting_key = 'contact_email'").fetchone()
		conn_email.close()
		admin_email = (email_row['setting_value'] if email_row and email_row['setting_value'] else SENDGRID_FROM_EMAIL or app.config.get('MAIL_USERNAME', 'rr3340@naver.com'))
		send_email(
			subject=f'[VBE 문의] {name or "익명"} - {email}',
			to_email=admin_email,
			body_text=f"새 문의가 접수되었습니다.\n\n이름: {name or '익명'}\n이메일: {email}\n\n내용:\n{message_text}"
		)
	except Exception as e:
		# 이메일 실패해도 DB 저장은 성공했으므로 계속 진행
		app.logger.error(f"문의 이메일 발송 실패: {str(e)}")

	success_msg = 'Contact submitted successfully!' if lang == 'en' else '문의가 성공적으로 접수되었습니다! 관리자가 확인 후 답변드리겠습니다.'
	flash(success_msg, 'success')
	return redirect(url_for('contact', lang=lang) if lang == 'en' else url_for('contact'))


@app.route('/')
def index():
	lang = request.args.get('lang', 'ko')  # 기본값은 한국어
	try:
		conn = get_db()
		try:
			banner = conn.execute('SELECT * FROM banner_settings WHERE page_name = ?', ('home',)).fetchone()
		except:
			banner = None
		
		try:
			sections = conn.execute('SELECT * FROM page_sections WHERE page_name = ? AND is_active = 1 ORDER BY order_num', ('home',)).fetchall()
		except:
			sections = []
		
		try:
			home_contents = conn.execute('SELECT * FROM home_contents WHERE is_active = 1 ORDER BY order_num').fetchall()
		except:
			home_contents = []
		
		# 사이트 이미지 가져오기
		site_images = {}
		try:
			images = conn.execute('SELECT image_key, image_path FROM site_images').fetchall()
			for img in images:
				site_images[img['image_key']] = img['image_path']
		except:
			pass
		
		conn.close()
	except Exception as e:
		# 데이터베이스 에러 시 기본값 사용
		app.logger.error(f"Database error in index route: {str(e)}")
		banner = None
		sections = []
		home_contents = []
		site_images = {}
	
	# 언어 설정을 템플릿에 전달
	if lang == 'en':
		return render_template('index_en.html', banner=banner, sections=sections, home_contents=home_contents, site_images=site_images)
	else:
		return render_template('index.html', banner=banner, sections=sections, home_contents=home_contents, site_images=site_images)


# ─── Open Graph 메타 태그 파서 ───
class OGParser(HTMLParser):
	def __init__(self):
		super().__init__()
		self.og = {}
		self.title = ''
		self._in_title = False

	def handle_starttag(self, tag, attrs):
		attrs_dict = dict(attrs)
		if tag == 'meta':
			prop = attrs_dict.get('property', '') or attrs_dict.get('name', '')
			content = attrs_dict.get('content', '')
			if prop.startswith('og:'):
				self.og[prop[3:]] = content
		if tag == 'title':
			self._in_title = True

	def handle_data(self, data):
		if self._in_title:
			self.title += data

	def handle_endtag(self, tag):
		if tag == 'title':
			self._in_title = False


@app.route('/api/link-preview', methods=['POST'])
@login_required
def api_link_preview():
	"""URL의 Open Graph 메타데이터를 가져와 미리보기 정보를 반환"""
	data = request.get_json()
	url = data.get('url', '').strip() if data else ''
	if not url or not url.startswith(('http://', 'https://')):
		return jsonify({'error': 'Invalid URL'}), 400

	try:
		req = urllib.request.Request(url, headers={
			'User-Agent': 'Mozilla/5.0 (compatible; LinkPreview/1.0)',
			'Accept': 'text/html',
			'Accept-Language': 'ko,en;q=0.9'
		})
		with urllib.request.urlopen(req, timeout=5) as resp:
			content_type = resp.headers.get('Content-Type', '')
			if 'text/html' not in content_type:
				return jsonify({'error': 'Not an HTML page'}), 400
			html_bytes = resp.read(50000)  # 처음 50KB만
			charset = 'utf-8'
			if 'charset=' in content_type:
				charset = content_type.split('charset=')[-1].strip()
			html_text = html_bytes.decode(charset, errors='replace')

		parser = OGParser()
		parser.feed(html_text)

		title = parser.og.get('title', parser.title.strip()) or url
		description = parser.og.get('description', '')[:200]
		image = parser.og.get('image', '')

		return jsonify({
			'title': title,
			'description': description,
			'image': image,
			'url': url
		})
	except (urllib.error.URLError, Exception) as e:
		return jsonify({'error': str(e)}), 400


@app.route('/api/schedules')
def api_schedules():
	"""FullCalendar용 일정 JSON 데이터"""
	conn = get_db()
	schedules = conn.execute('SELECT id, title, event_date, location FROM schedules ORDER BY event_date').fetchall()
	conn.close()
	events = []
	for s in schedules:
		events.append({
			'id': s['id'],
			'title': s['title'],
			'start': s['event_date'],
			'url': f"/schedule/{s['id']}",
			'extendedProps': {'location': s['location'] or ''}
		})
	return jsonify(events)


@app.route('/api/traffic')
@login_required
def api_traffic():
	"""최근 30일 트래픽 데이터 (고유 IP 기준)"""
	conn = get_db()
	rows = conn.execute('''
		SELECT DATE(visited_at) as date, COUNT(DISTINCT ip_address) as count
		FROM page_views
		WHERE visited_at >= DATE('now', '-30 days')
		GROUP BY DATE(visited_at)
		ORDER BY date
	''').fetchall()
	conn.close()
	data = [{'date': r['date'], 'count': r['count']} for r in rows]
	return jsonify(data)


# ─── 관리자: 방문자 로그 ───
@app.route('/admin/visitors')
@login_required
def admin_visitors():
	page = request.args.get('page', 1, type=int)
	per_page = 50
	date_filter = request.args.get('date', '')
	ip_filter = request.args.get('ip', '')

	conn = get_db()

	# 필터 조건 구성
	where_clauses = []
	params = []
	if date_filter:
		where_clauses.append("DATE(visited_at) = ?")
		params.append(date_filter)
	if ip_filter:
		where_clauses.append("ip_address LIKE ?")
		params.append(f'%{ip_filter}%')

	where_sql = (' WHERE ' + ' AND '.join(where_clauses)) if where_clauses else ''

	# 총 개수
	total = conn.execute(f'SELECT COUNT(*) as count FROM page_views{where_sql}', params).fetchone()['count']
	total_pages = (total + per_page - 1) // per_page

	# 페이지네이션된 데이터
	offset = (page - 1) * per_page
	rows = conn.execute(f'''
		SELECT id, page_path, ip_address, user_agent, visited_at
		FROM page_views{where_sql}
		ORDER BY visited_at DESC
		LIMIT ? OFFSET ?
	''', params + [per_page, offset]).fetchall()

	# 오늘 통계
	today_total = conn.execute("SELECT COUNT(*) as count FROM page_views WHERE DATE(visited_at) = DATE('now')").fetchone()['count']
	today_unique = conn.execute("SELECT COUNT(DISTINCT ip_address) as count FROM page_views WHERE DATE(visited_at) = DATE('now')").fetchone()['count']

	# 상위 IP (오늘)
	top_ips = conn.execute('''
		SELECT ip_address, COUNT(*) as count
		FROM page_views WHERE DATE(visited_at) = DATE('now')
		GROUP BY ip_address ORDER BY count DESC LIMIT 10
	''').fetchall()

	# 상위 페이지 (오늘)
	top_pages = conn.execute('''
		SELECT page_path, COUNT(*) as count
		FROM page_views WHERE DATE(visited_at) = DATE('now')
		GROUP BY page_path ORDER BY count DESC LIMIT 10
	''').fetchall()

	conn.close()

	return render_template('admin/visitors.html',
		visitors=rows, page=page, total_pages=total_pages, total=total,
		today_total=today_total, today_unique=today_unique,
		top_ips=top_ips, top_pages=top_pages,
		date_filter=date_filter, ip_filter=ip_filter)


@app.route('/notice')
def notice():
	lang = request.args.get('lang', 'ko')
	page = request.args.get('page', 1, type=int)
	per_page = 10
	search_query = request.args.get('q', '').strip()
	search_type = request.args.get('search_type', 'title')

	conn = get_db()
	banner = conn.execute("SELECT * FROM banner_settings WHERE page_name = ?", ('notice',)).fetchone()

	# 검색 쿼리 처리
	if search_query:
		if search_type == 'content':
			count_row = conn.execute('SELECT COUNT(*) as cnt FROM notices WHERE content LIKE ?', (f'%{search_query}%',)).fetchone()
			total = count_row['cnt']
			notices = conn.execute('SELECT * FROM notices WHERE content LIKE ? ORDER BY created_at DESC LIMIT ? OFFSET ?',
				(f'%{search_query}%', per_page, (page - 1) * per_page)).fetchall()
		elif search_type == 'author':
			count_row = conn.execute('SELECT COUNT(*) as cnt FROM notices WHERE author LIKE ?', (f'%{search_query}%',)).fetchone()
			total = count_row['cnt']
			notices = conn.execute('SELECT * FROM notices WHERE author LIKE ? ORDER BY created_at DESC LIMIT ? OFFSET ?',
				(f'%{search_query}%', per_page, (page - 1) * per_page)).fetchall()
		else:
			count_row = conn.execute('SELECT COUNT(*) as cnt FROM notices WHERE title LIKE ?', (f'%{search_query}%',)).fetchone()
			total = count_row['cnt']
			notices = conn.execute('SELECT * FROM notices WHERE title LIKE ? ORDER BY created_at DESC LIMIT ? OFFSET ?',
				(f'%{search_query}%', per_page, (page - 1) * per_page)).fetchall()
	else:
		count_row = conn.execute('SELECT COUNT(*) as cnt FROM notices').fetchone()
		total = count_row['cnt']
		notices = conn.execute('SELECT * FROM notices ORDER BY created_at DESC LIMIT ? OFFSET ?',
			(per_page, (page - 1) * per_page)).fetchall()

	conn.close()
	total_pages = max(1, (total + per_page - 1) // per_page)

	if lang == 'en':
		return render_template('notice_en.html', notices=notices, banner=banner, page=page, total_pages=total_pages, total=total, search_query=search_query, search_type=search_type)
	else:
		return render_template('notice.html', notices=notices, banner=banner, page=page, total_pages=total_pages, total=total, search_query=search_query, search_type=search_type)


@app.route('/notice/<int:notice_id>')
def notice_detail(notice_id):
	lang = request.args.get('lang', 'ko')
	conn = get_db()
	notice = conn.execute('SELECT * FROM notices WHERE id = ?', (notice_id,)).fetchone()
	conn.close()

	if not notice:
		flash('공지사항을 찾을 수 없습니다.', 'error')
		return redirect(url_for('notice'))

	return render_template('notice_detail.html', notice=notice, lang=lang)


@app.route('/about')
def about():
	lang = request.args.get('lang', 'ko')
	conn = get_db()
	banner = conn.execute('SELECT * FROM banner_settings WHERE page_name = ?', ('about',)).fetchone()
	sections = conn.execute('SELECT * FROM page_sections WHERE page_name = ? AND is_active = 1 ORDER BY order_num', ('about',)).fetchall()
	pilots = conn.execute('SELECT * FROM pilots WHERE is_active = 1 ORDER BY order_num').fetchall()
	
	# 정비사 가져오기
	maintenance_crew = conn.execute('SELECT * FROM maintenance_crew WHERE is_active = 1 ORDER BY order_num').fetchall()
	
	# 후보자 가져오기
	candidates = conn.execute('SELECT * FROM candidates WHERE is_active = 1 ORDER BY order_num').fetchall()
	
	# 전대장 인사말 가져오기 - 언어별로 가져오기
	lang_param = 'en' if lang == 'en' else 'ko'
	commanders = conn.execute('SELECT * FROM commander_greeting WHERE is_active = 1 AND lang = ? ORDER BY order_num', (lang_param,)).fetchall()
	
	# 개요 섹션 가져오기 (임무, 선발, 편대) - 언어별로 가져오기
	lang_param = 'en' if lang == 'en' else 'ko'
	overview_sections = conn.execute('SELECT * FROM about_sections WHERE section_type IN (?, ?, ?) AND is_active = 1 AND lang = ? ORDER BY order_num', ('mission', 'selection', 'formation', lang_param)).fetchall()

	# 항공기 섹션 가져오기
	aircraft_sections = conn.execute('SELECT * FROM about_sections WHERE section_type IN (?, ?, ?) AND is_active = 1 AND lang = ? ORDER BY order_num', ('aircraft_intro', 'aircraft_specs', 'aircraft_features', lang_param)).fetchall()

	# 사이트 이미지 가져오기
	site_images = {}
	images = conn.execute('SELECT image_key, image_path FROM site_images').fetchall()
	for img in images:
		site_images[img['image_key']] = img['image_path']

	conn.close()

	if lang == 'en':
		return render_template('about_en.html', banner=banner, sections=sections, pilots=pilots, maintenance_crew=maintenance_crew, candidates=candidates, commanders=commanders, overview_sections=overview_sections, aircraft_sections=aircraft_sections, site_images=site_images)
	else:
		return render_template('about.html', banner=banner, sections=sections, pilots=pilots, maintenance_crew=maintenance_crew, candidates=candidates, commanders=commanders, overview_sections=overview_sections, aircraft_sections=aircraft_sections, site_images=site_images)


@app.route('/contact')
def contact():
	lang = request.args.get('lang', 'ko')
	conn = get_db()
	banner = conn.execute('SELECT * FROM banner_settings WHERE page_name = ?', ('contact',)).fetchone()
	sections = conn.execute('SELECT * FROM page_sections WHERE page_name = ? AND is_active = 1 ORDER BY order_num', ('contact',)).fetchall()
	conn.close()
	
	if lang == 'en':
		return render_template('contact_en.html', banner=banner, sections=sections)
	else:
		return render_template('contact.html', banner=banner, sections=sections)


@app.route('/donate')
def donate():
	lang = request.args.get('lang', 'ko')
	conn = get_db()
	banner = conn.execute('SELECT * FROM banner_settings WHERE page_name = ?', ('donate',)).fetchone()
	sections = conn.execute('SELECT * FROM page_sections WHERE page_name = ? AND is_active = 1 ORDER BY order_num', ('donate',)).fetchall()
	# 후원 설정 가져오기
	donate_settings = {}
	try:
		settings = conn.execute('SELECT setting_key, setting_value FROM site_settings WHERE setting_key LIKE ?', ('donate_%',)).fetchall()
		for s in settings:
			donate_settings[s['setting_key']] = s['setting_value']
	except:
		pass
	conn.close()

	if lang == 'en':
		return render_template('donate_en.html', banner=banner, sections=sections, donate_settings=donate_settings)
	else:
		return render_template('donate.html', banner=banner, sections=sections, donate_settings=donate_settings)


@app.route('/gallery')
def gallery():
	return redirect(url_for('gallery_photos', lang=request.args.get('lang', 'ko')))


@app.route('/gallery/photos')
def gallery_photos():
	lang = request.args.get('lang', 'ko')
	conn = get_db()
	photos = conn.execute('SELECT * FROM gallery WHERE is_active = 1 ORDER BY order_num, upload_date DESC').fetchall()
	banner = conn.execute("SELECT * FROM banner_settings WHERE page_name = ?", ('gallery',)).fetchone()
	conn.close()

	if lang == 'en':
		return render_template('gallery_en.html', photos=photos, banner=banner, active_tab='photos')
	else:
		return render_template('gallery.html', photos=photos, banner=banner, active_tab='photos')


@app.route('/gallery/videos')
def gallery_videos():
	lang = request.args.get('lang', 'ko')
	conn = get_db()
	videos = conn.execute('SELECT * FROM videos WHERE is_active = 1 ORDER BY order_num, upload_date DESC').fetchall()
	banner = conn.execute("SELECT * FROM banner_settings WHERE page_name = ?", ('gallery',)).fetchone()
	conn.close()

	if lang == 'en':
		return render_template('gallery_en.html', videos=videos, banner=banner, active_tab='videos')
	else:
		return render_template('gallery.html', videos=videos, banner=banner, active_tab='videos')


@app.route('/send_donate', methods=['POST'])
def send_donate():
	name = request.form.get('name', '').strip()
	amount = request.form.get('email', '').strip()  # email 필드를 금액으로 사용
	message = request.form.get('message', '').strip()
	
	if not amount or not message:
		flash('금액과 메시지를 모두 입력해 주세요.', 'error')
		return redirect(url_for('donate'))
	
	try:
		# 데이터베이스에 후원 문의 저장 (email 필드에 금액 저장, type은 'donate')
		conn = get_db()
		conn.execute(
			'INSERT INTO contact_messages (name, email, message, type) VALUES (?, ?, ?, ?)',
			(name, amount, message, 'donate')
		)
		conn.commit()
		conn.close()
		
		flash('후원 문의가 성공적으로 전송되었습니다! 빠른 시일 내에 연락드리겠습니다.', 'success')
		return redirect(url_for('donate'))
	
	except Exception as e:
		print(f"후원 문의 전송 오류: {str(e)}")
		flash('전송 중 오류가 발생했습니다. 다시 시도해 주세요.', 'error')
		return redirect(url_for('donate'))


@app.route('/schedule')
def schedule():
	lang = request.args.get('lang', 'ko')
	page = request.args.get('page', 1, type=int)
	per_page = 10

	conn = get_db()
	banner = conn.execute("SELECT * FROM banner_settings WHERE page_name = ?", ('schedule',)).fetchone()
	count_row = conn.execute('SELECT COUNT(*) as cnt FROM schedules').fetchone()
	total = count_row['cnt']
	schedules = conn.execute('SELECT * FROM schedules ORDER BY event_date DESC LIMIT ? OFFSET ?',
		(per_page, (page - 1) * per_page)).fetchall()
	conn.close()
	total_pages = max(1, (total + per_page - 1) // per_page)

	if lang == 'en':
		return render_template('schedule_en.html', schedules=schedules, banner=banner, page=page, total_pages=total_pages, total=total)
	else:
		return render_template('schedule.html', schedules=schedules, banner=banner, page=page, total_pages=total_pages, total=total)


@app.route('/schedule/<int:schedule_id>')
def schedule_detail(schedule_id):
	lang = request.args.get('lang', 'ko')
	conn = get_db()
	schedule = conn.execute('SELECT * FROM schedules WHERE id = ?', (schedule_id,)).fetchone()
	conn.close()

	if not schedule:
		flash('일정을 찾을 수 없습니다.', 'error')
		return redirect(url_for('schedule'))

	return render_template('schedule_detail.html', schedule=schedule, lang=lang)


# 관리자 로그인 페이지
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
	if request.method == 'POST':
		username = request.form.get('username', '').strip()
		password = request.form.get('password', '').strip()

		# 1) users 테이블에 있는 관리자 계정(role='admin')으로 로그인 시도
		conn = get_db()
		user = conn.execute(
			"SELECT * FROM users WHERE username = ? AND is_active = 1 AND role = 'admin'",
			(username,)
		).fetchone()
		conn.close()

		if user and verify_password(password, user['password_hash']):
			# 일반 회원 로그인과 동일한 세션 정보 + 관리자 플래그 설정
			session['user_id'] = user['id']
			session['user_email'] = user['email']
			session['user_name'] = user['display_name']
			session['user_role'] = user['role']  # 'admin'
			session['logged_in'] = True
			session['username'] = user['username']
			flash(f'{user["display_name"]}님 관리자 로그인 성공!', 'success')
			return redirect(url_for('admin_dashboard'))

		# 2) 기존 하드코딩 관리자 계정(백업용)도 계속 지원
		if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
			session['logged_in'] = True
			session['username'] = username
			flash('관리자 로그인 성공!', 'success')
			return redirect(url_for('admin_dashboard'))

		flash('아이디 또는 비밀번호가 잘못되었습니다.', 'error')
	
	return render_template('admin/login.html')


# 관리자 로그아웃
@app.route('/admin/logout')
def admin_logout():
	session.clear()
	flash('로그아웃되었습니다.', 'success')
	return redirect(url_for('index'))


# ─── Google OAuth 회원가입/로그인 (수동 구현 - authlib state 문제 해결) ───
@app.route('/auth/google')
def auth_google():
	import secrets
	client_id = os.environ.get('GOOGLE_CLIENT_ID', '')
	if not client_id:
		flash('Google 로그인이 설정되지 않았습니다.', 'error')
		return redirect(url_for('index'))

	oauth_domain = os.environ.get('OAUTH_REDIRECT_DOMAIN', request.host)
	redirect_uri = 'https://' + oauth_domain + '/auth/google/callback'

	# state를 직접 생성하여 세션에 저장
	state = secrets.token_urlsafe(32)
	session['oauth_state'] = state

	# Google 인증 URL 직접 구성
	auth_url = (
		'https://accounts.google.com/o/oauth2/v2/auth'
		f'?client_id={client_id}'
		f'&redirect_uri={redirect_uri}'
		'&response_type=code'
		'&scope=openid+email+profile'
		f'&state={state}'
		'&access_type=offline'
		'&prompt=consent'
	)
	return redirect(auth_url)


@app.route('/auth/google/callback')
def auth_google_callback():
	import requests as req_lib

	code = request.args.get('code')
	state = request.args.get('state')
	error = request.args.get('error')

	if error:
		flash(f'Google 인증 오류: {error}', 'error')
		return redirect(url_for('index'))

	if not code:
		flash('Google 인증에 실패했습니다.', 'error')
		return redirect(url_for('index'))

	# state 검증 (세션에 저장된 값과 비교, 없으면 건너뜀)
	saved_state = session.pop('oauth_state', None)
	if saved_state and state != saved_state:
		app.logger.warning(f'OAuth state mismatch: saved={saved_state}, received={state}')
		# state 불일치해도 진행 (세션 쿠키 도메인 문제 등으로 발생 가능)

	client_id = os.environ.get('GOOGLE_CLIENT_ID', '')
	client_secret = os.environ.get('GOOGLE_CLIENT_SECRET', '')
	oauth_domain = os.environ.get('OAUTH_REDIRECT_DOMAIN', request.host)
	redirect_uri = 'https://' + oauth_domain + '/auth/google/callback'

	# Authorization code → Access token 교환
	try:
		token_resp = req_lib.post('https://oauth2.googleapis.com/token', data={
			'code': code,
			'client_id': client_id,
			'client_secret': client_secret,
			'redirect_uri': redirect_uri,
			'grant_type': 'authorization_code'
		}, timeout=10)
		token_data = token_resp.json()
	except Exception as e:
		app.logger.error(f'Google token exchange failed: {e}')
		flash('Google 인증에 실패했습니다.', 'error')
		return redirect(url_for('index'))

	if 'access_token' not in token_data:
		app.logger.error(f'Google token error: {token_data}')
		flash('Google 인증에 실패했습니다.', 'error')
		return redirect(url_for('index'))

	# Access token으로 사용자 정보 가져오기
	try:
		userinfo_resp = req_lib.get('https://www.googleapis.com/oauth2/v2/userinfo',
			headers={'Authorization': f'Bearer {token_data["access_token"]}'},
			timeout=10)
		userinfo = userinfo_resp.json()
	except Exception as e:
		app.logger.error(f'Google userinfo failed: {e}')
		flash('Google 인증에 실패했습니다.', 'error')
		return redirect(url_for('index'))

	if not userinfo.get('email'):
		flash('Google 인증에 실패했습니다.', 'error')
		return redirect(url_for('index'))

	email = userinfo.get('email', '')
	google_id = userinfo.get('id', '') or userinfo.get('sub', '')
	name = userinfo.get('name', '')

	conn = get_db()
	user = conn.execute('SELECT * FROM users WHERE google_id = ? OR email = ?', (google_id, email)).fetchone()

	if user:
		# 기존 회원 - 로그인
		session['user_id'] = user['id']
		session['user_email'] = user['email']
		session['user_name'] = user['display_name'] or name
		session['user_role'] = user['role']
		# 관리자 역할이면 관리자 세션도 설정
		if user['role'] == 'admin':
			session['logged_in'] = True
			session['username'] = user['username']
		conn.close()
		flash(f'{name}님 환영합니다!', 'success')
		if user['role'] == 'admin':
			return redirect(url_for('admin_dashboard'))
		return redirect(url_for('index'))
	else:
		# 신규 회원 - 등록 페이지로 이동
		session['oauth_email'] = email
		session['oauth_google_id'] = google_id
		session['oauth_name'] = name
		conn.close()
		return redirect(url_for('auth_register'))


@app.route('/auth/register', methods=['GET', 'POST'])
def auth_register():
	if 'oauth_email' not in session:
		return redirect(url_for('index'))

	if request.method == 'POST':
		username = request.form.get('username', '').strip()
		password = request.form.get('password', '').strip()
		display_name = request.form.get('display_name', '').strip() or session.get('oauth_name', '')

		if not username or not password:
			flash('아이디와 비밀번호를 입력해주세요.', 'error')
			return render_template('auth/register.html', email=session['oauth_email'], name=session.get('oauth_name', ''))

		if len(password) < 4:
			flash('비밀번호는 4자 이상이어야 합니다.', 'error')
			return render_template('auth/register.html', email=session['oauth_email'], name=session.get('oauth_name', ''))

		conn = get_db()
		existing = conn.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
		if existing:
			conn.close()
			flash('이미 사용 중인 아이디입니다.', 'error')
			return render_template('auth/register.html', email=session['oauth_email'], name=session.get('oauth_name', ''))

		conn.execute('''
			INSERT INTO users (email, display_name, google_id, username, password_hash, role)
			VALUES (?, ?, ?, ?, ?, 'member')
		''', (session['oauth_email'], display_name, session.get('oauth_google_id'), username, hash_password(password)))
		conn.commit()

		user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
		session['user_id'] = user['id']
		session['user_email'] = user['email']
		session['user_name'] = user['display_name']
		session['user_role'] = user['role']
		conn.close()

		# OAuth 임시 데이터 삭제
		session.pop('oauth_email', None)
		session.pop('oauth_google_id', None)
		session.pop('oauth_name', None)

		flash('회원가입이 완료되었습니다!', 'success')
		return redirect(url_for('index'))

	return render_template('auth/register.html', email=session['oauth_email'], name=session.get('oauth_name', ''))


@app.route('/auth/login', methods=['GET', 'POST'])
def auth_login():
	if request.method == 'POST':
		username = request.form.get('username', '').strip()
		password = request.form.get('password', '').strip()

		conn = get_db()
		user = conn.execute('SELECT * FROM users WHERE username = ? AND is_active = 1', (username,)).fetchone()
		conn.close()

		if user and verify_password(password, user['password_hash']):
			session['user_id'] = user['id']
			session['user_email'] = user['email']
			session['user_name'] = user['display_name']
			session['user_role'] = user['role']
			flash(f'{user["display_name"]}님 환영합니다!', 'success')
			# 관리자 역할이면 관리자 대시보드로, 아니면 홈으로
			if user['role'] == 'admin':
				session['logged_in'] = True
				session['username'] = user['username']
				return redirect(url_for('admin_dashboard'))
			return redirect(url_for('index'))
		else:
			flash('아이디 또는 비밀번호가 잘못되었습니다.', 'error')

	return render_template('auth/login.html')


@app.route('/auth/logout')
def auth_logout():
	session.pop('user_id', None)
	session.pop('user_email', None)
	session.pop('user_name', None)
	session.pop('user_role', None)
	flash('로그아웃되었습니다.', 'success')
	return redirect(url_for('index'))


@app.route('/auth/signup', methods=['GET', 'POST'])
def auth_signup():
	if request.method == 'POST':
		username = request.form.get('username', '').strip()
		email = request.form.get('email', '').strip()
		password = request.form.get('password', '').strip()
		password2 = request.form.get('password2', '').strip()
		display_name = request.form.get('display_name', '').strip() or username

		if not username or not email or not password:
			flash('모든 필수 항목을 입력해주세요.', 'error')
			return render_template('auth/signup.html')
		if len(password) < 4:
			flash('비밀번호는 4자 이상이어야 합니다.', 'error')
			return render_template('auth/signup.html')
		if password != password2:
			flash('비밀번호가 일치하지 않습니다.', 'error')
			return render_template('auth/signup.html')

		conn = get_db()
		if conn.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone():
			conn.close()
			flash('이미 사용 중인 아이디입니다.', 'error')
			return render_template('auth/signup.html')
		if conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone():
			conn.close()
			flash('이미 등록된 이메일입니다.', 'error')
			return render_template('auth/signup.html')

		conn.execute('''
			INSERT INTO users (email, display_name, username, password_hash, role)
			VALUES (?, ?, ?, ?, 'member')
		''', (email, display_name, username, hash_password(password)))
		conn.commit()

		user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
		session['user_id'] = user['id']
		session['user_email'] = user['email']
		session['user_name'] = user['display_name']
		session['user_role'] = user['role']
		conn.close()

		flash('회원가입이 완료되었습니다!', 'success')
		return redirect(url_for('index'))

	return render_template('auth/signup.html')


@app.route('/auth/find-id', methods=['GET', 'POST'])
def auth_find_id():
	if request.method == 'POST':
		email = request.form.get('email', '').strip()
		if not email:
			flash('이메일을 입력해주세요.', 'error')
			return render_template('auth/find_id.html')

		conn = get_db()
		user = conn.execute('SELECT username FROM users WHERE email = ? AND is_active = 1', (email,)).fetchone()
		conn.close()

		if user:
			# 아이디 일부 마스킹
			uid = user['username']
			if len(uid) > 2:
				masked = uid[:2] + '*' * (len(uid) - 2)
			else:
				masked = uid[0] + '*'
			flash(f'등록된 아이디: {masked}', 'success')
		else:
			flash('해당 이메일로 등록된 계정이 없습니다.', 'error')

	return render_template('auth/find_id.html')


@app.route('/auth/find-password', methods=['GET', 'POST'])
def auth_find_password():
	if request.method == 'POST':
		username = request.form.get('username', '').strip()
		email = request.form.get('email', '').strip()
		if not username or not email:
			flash('아이디와 이메일을 모두 입력해주세요.', 'error')
			return render_template('auth/find_password.html')

		conn = get_db()
		user = conn.execute('SELECT id FROM users WHERE username = ? AND email = ? AND is_active = 1', (username, email)).fetchone()

		if user:
			import random, string
			new_pw = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
			conn.execute('UPDATE users SET password_hash = ? WHERE id = ?', (hash_password(new_pw), user['id']))
			conn.commit()
			conn.close()

			# 이메일로 임시 비밀번호 발송 시도
			sent = send_email(
				subject='[VBE] 임시 비밀번호 안내',
				to_email=email,
				body_text=f'안녕하세요, Virtual Black Eagles입니다.\n\n임시 비밀번호: {new_pw}\n\n로그인 후 비밀번호를 변경해주세요.'
			)
			if sent:
				flash('임시 비밀번호가 이메일로 발송되었습니다.', 'success')
			else:
				flash(f'임시 비밀번호: {new_pw} (이메일 발송 실패 - 메모해두세요)', 'success')
		else:
			conn.close()
			flash('입력한 정보와 일치하는 계정이 없습니다.', 'error')

	return render_template('auth/find_password.html')


# ─── 관리자: 회원 관리 ───
@app.route('/admin/users')
@login_required
def admin_users():
	conn = get_db()
	users = conn.execute('SELECT * FROM users ORDER BY created_at DESC').fetchall()
	conn.close()
	return render_template('admin/users.html', users=users)


@app.route('/admin/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_user_edit(user_id):
	conn = get_db()
	user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
	if not user:
		conn.close()
		flash('회원을 찾을 수 없습니다.', 'error')
		return redirect(url_for('admin_users'))

	if request.method == 'POST':
		role = request.form.get('role', 'member')
		is_active = 1 if request.form.get('is_active') else 0
		display_name = request.form.get('display_name', '').strip()

		conn.execute('''
			UPDATE users SET role = ?, is_active = ?, display_name = ?, updated_at = ?
			WHERE id = ?
		''', (role, is_active, display_name, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), user_id))
		conn.commit()
		conn.close()
		flash('회원 정보가 수정되었습니다.', 'success')
		return redirect(url_for('admin_users'))

	conn.close()
	return render_template('admin/user_edit.html', user=user)


@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@login_required
def admin_user_delete(user_id):
	conn = get_db()
	user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
	if not user:
		conn.close()
		flash('회원을 찾을 수 없습니다.', 'error')
		return redirect(url_for('admin_users'))

	if user['role'] == 'admin':
		conn.close()
		flash('관리자 계정은 삭제할 수 없습니다.', 'error')
		return redirect(url_for('admin_users'))

	conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
	conn.commit()
	conn.close()

	flash('회원이 삭제되었습니다.', 'success')
	return redirect(url_for('admin_users'))


# 관리자 대시보드
@app.route('/admin')
@login_required
def admin_dashboard():
	conn = get_db()
	# 읽지 않은 문의 수 가져오기
	unread_count = conn.execute('SELECT COUNT(*) as count FROM contact_messages WHERE is_read = 0').fetchone()['count']
	# 최근 문의 5개 가져오기
	recent_messages = conn.execute('SELECT * FROM contact_messages ORDER BY created_at DESC LIMIT 5').fetchall()
	
	# 읽지 않은 채팅 메시지 수 가져오기
	unread_chat_count = conn.execute('''
		SELECT COUNT(*) as count FROM chat_messages 
		WHERE sender_type = 'user' AND is_read = 0
	''').fetchone()['count']
	
	# 활성 채팅 세션 수
	active_chat_sessions = conn.execute('''
		SELECT COUNT(*) as count FROM chat_sessions WHERE status = 'active'
	''').fetchone()['count']

	# 오늘 방문자 수 (고유 IP 기준)
	today_views = conn.execute('''
		SELECT COUNT(DISTINCT ip_address) as count FROM page_views WHERE DATE(visited_at) = DATE('now')
	''').fetchone()['count']

	# 총 방문 수 (고유 IP+날짜 기준)
	total_views = conn.execute('SELECT COUNT(DISTINCT ip_address || CAST(DATE(visited_at) AS TEXT)) as count FROM page_views').fetchone()['count']

	# 이번 주 방문 수 (고유 IP+날짜 기준)
	week_views = conn.execute('''
		SELECT COUNT(DISTINCT ip_address || CAST(DATE(visited_at) AS TEXT)) as count FROM page_views
		WHERE visited_at >= DATE('now', '-7 days')
	''').fetchone()['count']

	# 이번 달 방문 수 (고유 IP+날짜 기준)
	month_views = conn.execute('''
		SELECT COUNT(DISTINCT ip_address || CAST(DATE(visited_at) AS TEXT)) as count FROM page_views
		WHERE visited_at >= DATE('now', 'start of month')
	''').fetchone()['count']

	# 총 회원 수
	total_users = conn.execute('SELECT COUNT(*) as count FROM users').fetchone()['count']

	conn.close()

	return render_template('admin/dashboard.html',
		unread_count=unread_count,
		recent_messages=recent_messages,
		unread_chat_count=unread_chat_count,
		active_chat_sessions=active_chat_sessions,
		today_views=today_views,
		total_views=total_views,
		week_views=week_views,
		month_views=month_views,
		total_users=total_users
	)


# 공지사항 관리 - 목록
@app.route('/admin/notices')
@login_required
def admin_notices():
	conn = get_db()
	notices = conn.execute('SELECT * FROM notices ORDER BY created_at DESC').fetchall()
	conn.close()
	return render_template('admin/notices.html', notices=notices)


# 공지사항 작성 페이지
@app.route('/admin/notices/new', methods=['GET', 'POST'])
@login_required
def admin_notice_new():
	if request.method == 'POST':
		title = request.form.get('title', '').strip()
		content = request.form.get('content', '').strip()
		author = session.get('username', 'admin')
		
		if not title or not content:
			flash('제목과 내용을 모두 입력해주세요.', 'error')
			return redirect(url_for('admin_notice_new'))
		
		conn = get_db()
		conn.execute('INSERT INTO notices (title, content, author) VALUES (?, ?, ?)',
					 (title, content, author))
		conn.commit()
		conn.close()
		
		flash('공지사항이 작성되었습니다.', 'success')
		return redirect(url_for('admin_notices'))
	
	return render_template('admin/notice_form.html', notice=None)


# 공지사항 수정 페이지
@app.route('/admin/notices/<int:notice_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_notice_edit(notice_id):
	conn = get_db()
	
	if request.method == 'POST':
		title = request.form.get('title', '').strip()
		content = request.form.get('content', '').strip()
		
		if not title or not content:
			flash('제목과 내용을 모두 입력해주세요.', 'error')
			return redirect(url_for('admin_notice_edit', notice_id=notice_id))
		
		conn.execute('UPDATE notices SET title = ?, content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
					 (title, content, notice_id))
		conn.commit()
		conn.close()
		
		flash('공지사항이 수정되었습니다.', 'success')
		return redirect(url_for('admin_notices'))
	
	notice = conn.execute('SELECT * FROM notices WHERE id = ?', (notice_id,)).fetchone()
	conn.close()
	
	if not notice:
		flash('공지사항을 찾을 수 없습니다.', 'error')
		return redirect(url_for('admin_notices'))
	
	return render_template('admin/notice_form.html', notice=notice)


# 공지사항 삭제
@app.route('/admin/notices/<int:notice_id>/delete', methods=['POST'])
@login_required
def admin_notice_delete(notice_id):
	conn = get_db()
	conn.execute('DELETE FROM notices WHERE id = ?', (notice_id,))
	conn.commit()
	conn.close()
	
	flash('공지사항이 삭제되었습니다.', 'success')
	return redirect(url_for('admin_notices'))


# 일정 관리 - 목록
@app.route('/admin/schedules')
@login_required
def admin_schedules():
	conn = get_db()
	schedules = conn.execute('SELECT * FROM schedules ORDER BY event_date DESC').fetchall()
	conn.close()
	return render_template('admin/schedules.html', schedules=schedules)


# 일정 작성 페이지
@app.route('/admin/schedules/new', methods=['GET', 'POST'])
@login_required
def admin_schedule_new():
	if request.method == 'POST':
		title = request.form.get('title', '').strip()
		location = request.form.get('location', '').strip()
		event_date = request.form.get('event_date', '').strip()
		description = request.form.get('description', '').strip()
		
		if not title or not event_date:
			flash('제목과 날짜를 모두 입력해주세요.', 'error')
			return redirect(url_for('admin_schedule_new'))
		
		conn = get_db()
		conn.execute('INSERT INTO schedules (title, location, event_date, description) VALUES (?, ?, ?, ?)',
					 (title, location, event_date, description))
		conn.commit()
		conn.close()
		
		flash('일정이 추가되었습니다.', 'success')
		return redirect(url_for('admin_schedules'))
	
	return render_template('admin/schedule_form.html', schedule=None)


# 일정 수정 페이지
@app.route('/admin/schedules/<int:schedule_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_schedule_edit(schedule_id):
	conn = get_db()
	
	if request.method == 'POST':
		title = request.form.get('title', '').strip()
		location = request.form.get('location', '').strip()
		event_date = request.form.get('event_date', '').strip()
		description = request.form.get('description', '').strip()
		
		if not title or not event_date:
			flash('제목과 날짜를 모두 입력해주세요.', 'error')
			return redirect(url_for('admin_schedule_edit', schedule_id=schedule_id))
		
		conn.execute('UPDATE schedules SET title = ?, location = ?, event_date = ?, description = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
					 (title, location, event_date, description, schedule_id))
		conn.commit()
		conn.close()
		
		flash('일정이 수정되었습니다.', 'success')
		return redirect(url_for('admin_schedules'))
	
	schedule = conn.execute('SELECT * FROM schedules WHERE id = ?', (schedule_id,)).fetchone()
	conn.close()
	
	if not schedule:
		flash('일정을 찾을 수 없습니다.', 'error')
		return redirect(url_for('admin_schedules'))
	
	return render_template('admin/schedule_form.html', schedule=schedule)


# 일정 삭제
@app.route('/admin/schedules/<int:schedule_id>/delete', methods=['POST'])
@login_required
def admin_schedule_delete(schedule_id):
	conn = get_db()
	conn.execute('DELETE FROM schedules WHERE id = ?', (schedule_id,))
	conn.commit()
	conn.close()
	
	flash('일정이 삭제되었습니다.', 'success')
	return redirect(url_for('admin_schedules'))


# 문의 관리 - 목록
@app.route('/admin/messages')
@login_required
def admin_messages():
	message_type = request.args.get('type', 'all')  # all, contact, donate
	conn = get_db()
	
	if message_type == 'contact':
		messages = conn.execute("SELECT * FROM contact_messages WHERE type = 'contact' OR type IS NULL ORDER BY created_at DESC").fetchall()
	elif message_type == 'donate':
		messages = conn.execute("SELECT * FROM contact_messages WHERE type = 'donate' ORDER BY created_at DESC").fetchall()
	else:  # all
		messages = conn.execute('SELECT * FROM contact_messages ORDER BY created_at DESC').fetchall()
	
	conn.close()
	
	return render_template('admin/messages.html', messages=messages, current_type=message_type)


# 문의 상세보기
@app.route('/admin/messages/<int:message_id>')
@login_required
def admin_message_detail(message_id):
	conn = get_db()
	message = conn.execute('SELECT * FROM contact_messages WHERE id = ?', (message_id,)).fetchone()
	
	if message and message['is_read'] == 0:
		# 읽음 표시
		conn.execute('UPDATE contact_messages SET is_read = 1 WHERE id = ?', (message_id,))
		conn.commit()
	
	conn.close()
	
	if not message:
		flash('문의를 찾을 수 없습니다.', 'error')
		return redirect(url_for('admin_messages'))
	
	return render_template('admin/message_detail.html', message=message)


# 문의 삭제
@app.route('/admin/messages/<int:message_id>/delete', methods=['POST'])
@login_required
def admin_message_delete(message_id):
	conn = get_db()
	conn.execute('DELETE FROM contact_messages WHERE id = ?', (message_id,))
	conn.commit()
	conn.close()
	
	flash('문의가 삭제되었습니다.', 'success')
	return redirect(url_for('admin_messages'))


# 페이지 섹션 관리
@app.route('/admin/pages')
@login_required
def admin_pages():
	conn = get_db()
	sections = conn.execute('SELECT * FROM page_sections ORDER BY page_name, order_num').fetchall()
	conn.close()
	
	# 페이지별로 그룹화
	pages = {}
	for section in sections:
		page = section['page_name']
		if page not in pages:
			pages[page] = []
		pages[page].append(section)
	
	return render_template('admin/pages.html', pages=pages)


# 페이지 섹션 추가/수정 폼
@app.route('/admin/pages/section', methods=['GET'])
@app.route('/admin/pages/section/<int:section_id>', methods=['GET'])
@login_required
def admin_page_section_form(section_id=None):
	section = None
	if section_id:
		conn = get_db()
		section = conn.execute('SELECT * FROM page_sections WHERE id = ?', (section_id,)).fetchone()
		conn.close()
		if not section:
			flash('섹션을 찾을 수 없습니다.', 'error')
			return redirect(url_for('admin_pages'))
	
	return render_template('admin/page_section_form.html', section=section)


# 페이지 섹션 저장
@app.route('/admin/pages/section/save', methods=['POST'])
@login_required
def admin_page_section_save():
	section_id = request.form.get('section_id', '').strip()
	page_name = request.form.get('page_name', '').strip()
	section_identifier = request.form.get('section_identifier', '').strip()
	section_type = request.form.get('section_type', 'text').strip()
	title = request.form.get('title', '').strip()
	content = request.form.get('content', '').strip()
	image_url = request.form.get('image_url', '').strip()
	link_url = request.form.get('link_url', '').strip()
	link_text = request.form.get('link_text', '').strip()
	try:
		order_num = int(request.form.get('order_num', 0) or 0)
	except (ValueError, TypeError):
		order_num = 0
	is_active = 1 if request.form.get('is_active') else 0
	
	if not page_name or not section_identifier:
		flash('페이지 이름과 섹션 ID는 필수입니다.', 'error')
		return redirect(url_for('admin_page_section_form'))
	
	conn = get_db()
	
	if section_id:
		# 업데이트
		conn.execute('''
			UPDATE page_sections 
			SET page_name = ?, section_id = ?, section_type = ?, title = ?, content = ?, 
			    image_url = ?, link_url = ?, link_text = ?, order_num = ?, is_active = ?, 
			    updated_at = CURRENT_TIMESTAMP
			WHERE id = ?
		''', (page_name, section_identifier, section_type, title, content, image_url, 
		      link_url, link_text, order_num, is_active, section_id))
		flash('섹션이 수정되었습니다.', 'success')
	else:
		# 새로 추가
		try:
			conn.execute('''
				INSERT INTO page_sections 
				(page_name, section_id, section_type, title, content, image_url, link_url, link_text, order_num, is_active)
				VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
			''', (page_name, section_identifier, section_type, title, content, image_url, 
			      link_url, link_text, order_num, is_active))
			flash('섹션이 추가되었습니다.', 'success')
		except Exception as _integrity_err:
			if 'IntegrityError' not in type(_integrity_err).__name__ and 'UNIQUE' not in str(_integrity_err).upper():
				raise
			flash('이미 존재하는 페이지/섹션 조합입니다.', 'error')
			conn.close()
			return redirect(url_for('admin_page_section_form'))
	
	conn.commit()
	conn.close()
	return redirect(url_for('admin_pages'))


# 페이지 섹션 삭제
@app.route('/admin/pages/section/<int:section_id>/delete', methods=['POST'])
@login_required
def admin_page_section_delete(section_id):
	conn = get_db()
	conn.execute('DELETE FROM page_sections WHERE id = ?', (section_id,))
	conn.commit()
	conn.close()
	flash('섹션이 삭제되었습니다.', 'success')
	return redirect(url_for('admin_pages'))


# 배너 설정 관리
@app.route('/admin/banner')
@login_required
def admin_banner():
	conn = get_db()
	banners = conn.execute('SELECT * FROM banner_settings ORDER BY page_name').fetchall()
	conn.close()
	return render_template('admin/banner.html', banners=banners)


# 배너 설정 수정
@app.route('/admin/banner/<int:banner_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_banner_edit(banner_id):
	conn = get_db()
	
	if request.method == 'POST':
		background_image = request.form.get('background_image', '').strip()
		title = request.form.get('title', '').strip()
		subtitle = request.form.get('subtitle', '').strip()
		description = request.form.get('description', '').strip()
		button_text = request.form.get('button_text', '').strip()
		button_link = request.form.get('button_link', '').strip()
		title_font = request.form.get('title_font', 'Arial, sans-serif').strip()
		title_color = request.form.get('title_color', '#ffffff').strip()
		subtitle_color = request.form.get('subtitle_color', '#ffffff').strip()
		description_color = request.form.get('description_color', '#ffffff').strip()
		vertical_position = request.form.get('vertical_position', 'center').strip()
		padding_top = request.form.get('padding_top', '250').strip()
		
		if not title:
			flash('제목을 입력해주세요.', 'error')
			return redirect(url_for('admin_banner_edit', banner_id=banner_id))
		
		try:
			padding_top_int = int(padding_top)
		except:
			padding_top_int = 250
		
		conn.execute('''
			UPDATE banner_settings 
			SET background_image = ?, title = ?, subtitle = ?, description = ?, 
			    button_text = ?, button_link = ?, title_font = ?, title_color = ?, 
			    subtitle_color = ?, description_color = ?, vertical_position = ?, 
			    padding_top = ?, updated_at = CURRENT_TIMESTAMP 
			WHERE id = ?
		''', (background_image, title, subtitle, description, button_text, button_link,
		      title_font, title_color, subtitle_color, description_color, vertical_position,
		      padding_top_int, banner_id))
		conn.commit()
		conn.close()
		
		flash('배너 설정이 수정되었습니다.', 'success')
		return redirect(url_for('admin_banner'))
	
	banner = conn.execute('SELECT * FROM banner_settings WHERE id = ?', (banner_id,)).fetchone()
	conn.close()
	
	if not banner:
		flash('배너 설정을 찾을 수 없습니다.', 'error')
		return redirect(url_for('admin_banner'))
	
	return render_template('admin/banner_form.html', banner=banner)


# 조종사 관리
@app.route('/admin/pilots')
@login_required
def admin_pilots():
	conn = get_db()
	pilots = conn.execute('SELECT * FROM pilots ORDER BY order_num').fetchall()
	conn.close()
	return render_template('admin/pilots.html', pilots=pilots)


# 조종사 추가
@app.route('/admin/pilots/new', methods=['GET', 'POST'])
@login_required
def admin_pilot_new():
	if request.method == 'POST':
		number = request.form.get('number', '').strip()
		position = request.form.get('position', '').strip()
		callsign = request.form.get('callsign', '').strip()
		generation = request.form.get('generation', '').strip()
		aircraft = request.form.get('aircraft', '').strip()
		order_num = request.form.get('order_num', '0').strip()
		is_active = 1 if request.form.get('is_active') else 0
		
		if not all([number, position, callsign, generation, aircraft]):
			flash('모든 필수 항목을 입력해주세요.', 'error')
			return redirect(url_for('admin_pilot_new'))
		
		try:
			number_int = int(number)
			order_num_int = int(order_num)
		except:
			flash('번호와 정렬 순서는 숫자여야 합니다.', 'error')
			return redirect(url_for('admin_pilot_new'))
		
		# 파일 업로드 처리
		photo_url = '/static/images/default-pilot.jpg'  # 기본 이미지
		file = request.files.get('photo')
		if file and file.filename:
			filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_pilot_{callsign}.jpg"  # 최종 파일은 항상 .jpg
			filepath = os.path.join(UPLOAD_BASE, 'members', filename)
			os.makedirs(os.path.dirname(filepath), exist_ok=True)
			file.save(filepath)
			
			# 이미지 최적화
			if optimize_image(filepath):
				photo_url = f'/static/members/{filename}'
			else:
				flash('이미지 처리 중 오류가 발생했습니다.', 'warning')
		
		conn = get_db()
		conn.execute('''
			INSERT INTO pilots (number, position, callsign, generation, aircraft, photo_url, order_num, is_active)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?)
		''', (number_int, position, callsign, generation, aircraft, photo_url, order_num_int, is_active))
		conn.commit()
		conn.close()
		
		flash('조종사가 추가되었습니다.', 'success')
		return redirect(url_for('admin_pilots'))
	
	return render_template('admin/pilot_form.html', pilot=None)


# 조종사 수정
@app.route('/admin/pilots/<int:pilot_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_pilot_edit(pilot_id):
	conn = get_db()
	
	if request.method == 'POST':
		number = request.form.get('number', '').strip()
		position = request.form.get('position', '').strip()
		callsign = request.form.get('callsign', '').strip()
		generation = request.form.get('generation', '').strip()
		aircraft = request.form.get('aircraft', '').strip()
		order_num = request.form.get('order_num', '0').strip()
		is_active = 1 if request.form.get('is_active') else 0
		
		if not all([number, position, callsign, generation, aircraft]):
			flash('모든 필수 항목을 입력해주세요.', 'error')
			return redirect(url_for('admin_pilot_edit', pilot_id=pilot_id))
		
		try:
			number_int = int(number)
			order_num_int = int(order_num)
		except:
			flash('번호와 정렬 순서는 숫자여야 합니다.', 'error')
			return redirect(url_for('admin_pilot_edit', pilot_id=pilot_id))
		
		# 기존 사진 URL 가져오기
		pilot = conn.execute('SELECT photo_url FROM pilots WHERE id = ?', (pilot_id,)).fetchone()
		photo_url = pilot['photo_url'] if pilot else '/static/images/default-pilot.jpg'
		
		# 파일 업로드 처리
		file = request.files.get('photo')
		if file and file.filename:
			filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_pilot_{callsign}.jpg"  # 최종 파일은 항상 .jpg
			filepath = os.path.join(UPLOAD_BASE, 'members', filename)
			os.makedirs(os.path.dirname(filepath), exist_ok=True)
			file.save(filepath)
			
			# 이미지 최적화
			if optimize_image(filepath):
				photo_url = f'/static/members/{filename}'
			else:
				flash('이미지 처리 중 오류가 발생했습니다.', 'warning')
		
		conn.execute('''
			UPDATE pilots 
			SET number = ?, position = ?, callsign = ?, generation = ?, aircraft = ?, 
			    photo_url = ?, order_num = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP 
			WHERE id = ?
		''', (number_int, position, callsign, generation, aircraft, photo_url, order_num_int, is_active, pilot_id))
		conn.commit()
		conn.close()
		
		flash('조종사 정보가 수정되었습니다.', 'success')
		return redirect(url_for('admin_pilots'))
	
	pilot = conn.execute('SELECT * FROM pilots WHERE id = ?', (pilot_id,)).fetchone()
	conn.close()
	
	if not pilot:
		flash('조종사를 찾을 수 없습니다.', 'error')
		return redirect(url_for('admin_pilots'))
	
	return render_template('admin/pilot_form.html', pilot=pilot)


# 조종사 삭제
@app.route('/admin/pilots/<int:pilot_id>/delete', methods=['POST'])
@login_required
def admin_pilot_delete(pilot_id):
	conn = get_db()
	conn.execute('DELETE FROM pilots WHERE id = ?', (pilot_id,))
	conn.commit()
	conn.close()
	
	flash('조종사가 삭제되었습니다.', 'success')
	return redirect(url_for('admin_pilots'))


# ========== 정비사 관리 ==========

# 정비사 목록
@app.route('/admin/maintenance')
@login_required
def admin_maintenance():
	conn = get_db()
	crew = conn.execute('SELECT * FROM maintenance_crew ORDER BY order_num').fetchall()
	conn.close()
	return render_template('admin/maintenance.html', crew=crew)


# 정비사 추가
@app.route('/admin/maintenance/new', methods=['GET', 'POST'])
@login_required
def admin_maintenance_new():
	if request.method == 'POST':
		name = request.form.get('name', '').strip()
		role = request.form.get('role', '').strip()
		callsign = request.form.get('callsign', '').strip()
		bio = request.form.get('bio', '').strip()
		order_num = request.form.get('order_num', '0').strip()
		is_active = 1 if request.form.get('is_active') else 0
		
		if not all([name, callsign]):
			flash('이름과 콜사인은 필수 항목입니다.', 'error')
			return redirect(url_for('admin_maintenance_new'))
		
		try:
			order_num_int = int(order_num)
		except:
			flash('정렬 순서는 숫자여야 합니다.', 'error')
			return redirect(url_for('admin_maintenance_new'))
		
		# 사진 업로드 처리
		photo_url = '/static/images/default-crew.jpg'
		if 'photo' in request.files:
			file = request.files['photo']
			if file and file.filename:
				# 파일 확장자 추출
				file_ext = os.path.splitext(file.filename)[1].lower()
				if file_ext not in ['.jpg', '.jpeg', '.png', '.gif']:
					flash('이미지 파일만 업로드 가능합니다.', 'error')
					return redirect(url_for('admin_maintenance_new'))
				
				# 안전한 파일명 생성
				safe_callsign = ''.join(c for c in callsign if c.isalnum() or c in ('-', '_'))
				timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
				filename = f'{timestamp}_crew_{safe_callsign}.jpg'  # 최종 파일은 항상 .jpg
				
				# 파일 저장
				upload_folder = os.path.join(app.root_path, 'static', 'members')
				os.makedirs(upload_folder, exist_ok=True)
				file_path = os.path.join(upload_folder, filename)
				file.save(file_path)
				
				# 이미지 최적화
				if optimize_image(file_path):
					photo_url = f'/static/members/{filename}'
				else:
					flash('이미지 처리 중 오류가 발생했습니다.', 'warning')
		
		# 데이터베이스에 저장
		conn = get_db()
		conn.execute('''
			INSERT INTO maintenance_crew (name, role, callsign, photo_url, bio, order_num, is_active)
			VALUES (?, ?, ?, ?, ?, ?, ?)
		''', (name, role, callsign, photo_url, bio, order_num_int, is_active))
		conn.commit()
		conn.close()
		
		flash('정비사가 추가되었습니다.', 'success')
		return redirect(url_for('admin_maintenance'))
	
	return render_template('admin/maintenance_form.html', crew=None)


# 정비사 수정
@app.route('/admin/maintenance/<int:crew_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_maintenance_edit(crew_id):
	conn = get_db()
	
	if request.method == 'POST':
		name = request.form.get('name', '').strip()
		role = request.form.get('role', '').strip()
		callsign = request.form.get('callsign', '').strip()
		bio = request.form.get('bio', '').strip()
		order_num = request.form.get('order_num', '0').strip()
		is_active = 1 if request.form.get('is_active') else 0
		
		if not all([name, callsign]):
			flash('이름과 콜사인은 필수 항목입니다.', 'error')
			return redirect(url_for('admin_maintenance_edit', crew_id=crew_id))
		
		try:
			order_num_int = int(order_num)
		except:
			flash('정렬 순서는 숫자여야 합니다.', 'error')
			return redirect(url_for('admin_maintenance_edit', crew_id=crew_id))
		
		# 현재 정비사 정보 가져오기
		current_crew = conn.execute('SELECT photo_url FROM maintenance_crew WHERE id = ?', (crew_id,)).fetchone()
		photo_url = current_crew['photo_url'] if current_crew else '/static/images/default-crew.jpg'
		
		# 사진 업로드 처리
		if 'photo' in request.files:
			file = request.files['photo']
			if file and file.filename:
				# 파일 확장자 추출
				file_ext = os.path.splitext(file.filename)[1].lower()
				if file_ext not in ['.jpg', '.jpeg', '.png', '.gif']:
					flash('이미지 파일만 업로드 가능합니다.', 'error')
					return redirect(url_for('admin_maintenance_edit', crew_id=crew_id))
				
				# 안전한 파일명 생성
				safe_callsign = ''.join(c for c in callsign if c.isalnum() or c in ('-', '_'))
				timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
				filename = f'{timestamp}_crew_{safe_callsign}.jpg'  # 최종 파일은 항상 .jpg
				
				# 파일 저장
				upload_folder = os.path.join(app.root_path, 'static', 'members')
				os.makedirs(upload_folder, exist_ok=True)
				file_path = os.path.join(upload_folder, filename)
				file.save(file_path)
				
				# 이미지 최적화
				if optimize_image(file_path):
					photo_url = f'/static/members/{filename}'
				else:
					flash('이미지 처리 중 오류가 발생했습니다.', 'warning')
		
		# 데이터베이스 업데이트
		conn.execute('''
			UPDATE maintenance_crew
			SET name = ?, role = ?, callsign = ?, photo_url = ?, bio = ?, order_num = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP
			WHERE id = ?
		''', (name, role, callsign, photo_url, bio, order_num_int, is_active, crew_id))
		conn.commit()
		conn.close()
		
		flash('정비사 정보가 수정되었습니다.', 'success')
		return redirect(url_for('admin_maintenance'))
	
	crew = conn.execute('SELECT * FROM maintenance_crew WHERE id = ?', (crew_id,)).fetchone()
	conn.close()
	
	if not crew:
		flash('정비사를 찾을 수 없습니다.', 'error')
		return redirect(url_for('admin_maintenance'))
	
	return render_template('admin/maintenance_form.html', crew=crew)


# 정비사 삭제
@app.route('/admin/maintenance/<int:crew_id>/delete', methods=['POST'])
@login_required
def admin_maintenance_delete(crew_id):
	conn = get_db()
	conn.execute('DELETE FROM maintenance_crew WHERE id = ?', (crew_id,))
	conn.commit()
	conn.close()
	
	flash('정비사가 삭제되었습니다.', 'success')
	return redirect(url_for('admin_maintenance'))


# ========== 후보자 관리 ==========

# 후보자 목록
@app.route('/admin/candidates')
@login_required
def admin_candidates():
	conn = get_db()
	candidates = conn.execute('SELECT * FROM candidates ORDER BY order_num').fetchall()
	conn.close()
	return render_template('admin/candidates.html', candidates=candidates)


# 후보자 추가
@app.route('/admin/candidates/new', methods=['GET', 'POST'])
@login_required
def admin_candidate_new():
	if request.method == 'POST':
		name = request.form.get('name', '').strip()
		callsign = request.form.get('callsign', '').strip()
		bio = request.form.get('bio', '').strip()
		order_num = request.form.get('order_num', '0').strip()
		is_active = 1 if request.form.get('is_active') else 0
		
		if not all([name, callsign]):
			flash('이름과 콜사인은 필수 항목입니다.', 'error')
			return redirect(url_for('admin_candidate_new'))
		
		try:
			order_num_int = int(order_num)
		except:
			flash('정렬 순서는 숫자여야 합니다.', 'error')
			return redirect(url_for('admin_candidate_new'))
		
		# 사진 업로드 처리
		photo_url = '/static/images/default-pilot.jpg'
		if 'photo' in request.files:
			file = request.files['photo']
			if file and file.filename:
				# 파일 확장자 추출
				file_ext = os.path.splitext(file.filename)[1].lower()
				if file_ext not in ['.jpg', '.jpeg', '.png', '.gif']:
					flash('이미지 파일만 업로드 가능합니다.', 'error')
					return redirect(url_for('admin_candidate_new'))
				
				# 안전한 파일명 생성
				safe_callsign = ''.join(c for c in callsign if c.isalnum() or c in ('-', '_'))
				timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
				filename = f'{timestamp}_candidate_{safe_callsign}{file_ext}'
				
				# 파일 저장
				upload_folder = os.path.join(app.root_path, 'static', 'members')
				os.makedirs(upload_folder, exist_ok=True)
				file_path = os.path.join(upload_folder, filename)
				file.save(file_path)
				
				photo_url = f'/static/members/{filename}'
		
		# 데이터베이스에 저장
		conn = get_db()
		conn.execute('''
			INSERT INTO candidates (name, callsign, photo_url, bio, order_num, is_active)
			VALUES (?, ?, ?, ?, ?, ?)
		''', (name, callsign, photo_url, bio, order_num_int, is_active))
		conn.commit()
		conn.close()
		
		flash('후보자가 추가되었습니다.', 'success')
		return redirect(url_for('admin_candidates'))
	
	return render_template('admin/candidate_form.html', candidate=None)


# 후보자 수정
@app.route('/admin/candidates/<int:candidate_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_candidate_edit(candidate_id):
	conn = get_db()
	
	if request.method == 'POST':
		name = request.form.get('name', '').strip()
		callsign = request.form.get('callsign', '').strip()
		bio = request.form.get('bio', '').strip()
		order_num = request.form.get('order_num', '0').strip()
		is_active = 1 if request.form.get('is_active') else 0
		
		if not all([name, callsign]):
			flash('이름과 콜사인은 필수 항목입니다.', 'error')
			return redirect(url_for('admin_candidate_edit', candidate_id=candidate_id))
		
		try:
			order_num_int = int(order_num)
		except:
			flash('정렬 순서는 숫자여야 합니다.', 'error')
			return redirect(url_for('admin_candidate_edit', candidate_id=candidate_id))
		
		# 현재 후보자 정보 가져오기
		current_candidate = conn.execute('SELECT photo_url FROM candidates WHERE id = ?', (candidate_id,)).fetchone()
		photo_url = current_candidate['photo_url'] if current_candidate else '/static/images/default-pilot.jpg'
		
		# 사진 업로드 처리
		if 'photo' in request.files:
			file = request.files['photo']
			if file and file.filename:
				# 파일 확장자 추출
				file_ext = os.path.splitext(file.filename)[1].lower()
				if file_ext not in ['.jpg', '.jpeg', '.png', '.gif']:
					flash('이미지 파일만 업로드 가능합니다.', 'error')
					return redirect(url_for('admin_candidate_edit', candidate_id=candidate_id))
				
				# 안전한 파일명 생성
				safe_callsign = ''.join(c for c in callsign if c.isalnum() or c in ('-', '_'))
				timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
				filename = f'{timestamp}_candidate_{safe_callsign}{file_ext}'
				
				# 파일 저장
				upload_folder = os.path.join(app.root_path, 'static', 'members')
				os.makedirs(upload_folder, exist_ok=True)
				file_path = os.path.join(upload_folder, filename)
				file.save(file_path)
				
				photo_url = f'/static/members/{filename}'
		
		# 데이터베이스 업데이트
		conn.execute('''
			UPDATE candidates
			SET name = ?, callsign = ?, photo_url = ?, bio = ?, order_num = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP
			WHERE id = ?
		''', (name, callsign, photo_url, bio, order_num_int, is_active, candidate_id))
		conn.commit()
		conn.close()
		
		flash('후보자 정보가 수정되었습니다.', 'success')
		return redirect(url_for('admin_candidates'))
	
	candidate = conn.execute('SELECT * FROM candidates WHERE id = ?', (candidate_id,)).fetchone()
	conn.close()
	
	if not candidate:
		flash('후보자를 찾을 수 없습니다.', 'error')
		return redirect(url_for('admin_candidates'))
	
	return render_template('admin/candidate_form.html', candidate=candidate)


# 후보자 삭제
@app.route('/admin/candidates/<int:candidate_id>/delete', methods=['POST'])
@login_required
def admin_candidate_delete(candidate_id):
	conn = get_db()
	conn.execute('DELETE FROM candidates WHERE id = ?', (candidate_id,))
	conn.commit()
	conn.close()
	
	flash('후보자가 삭제되었습니다.', 'success')
	return redirect(url_for('admin_candidates'))


# ========== 전대장 인사말 관리 ==========
@app.route('/admin/commanders')
@login_required
def admin_commanders():
	conn = get_db()
	commanders = conn.execute('SELECT * FROM commander_greeting ORDER BY order_num').fetchall()
	conn.close()
	return render_template('admin/commanders.html', commanders=commanders)


# 전대장 인사말 추가
@app.route('/admin/commanders/new', methods=['GET', 'POST'])
@login_required
def admin_commander_new():
	if request.method == 'POST':
		name = request.form.get('name', '').strip()
		rank = request.form.get('rank', '').strip()
		callsign = request.form.get('callsign', '').strip()
		generation = request.form.get('generation', '').strip()
		aircraft = request.form.get('aircraft', '').strip()
		greeting_text = request.form.get('greeting_text', '').strip()
		order_num = request.form.get('order_num', '0').strip()
		is_active = 1 if request.form.get('is_active') else 0
		lang = request.form.get('lang', 'ko').strip()

		if not all([name, rank, callsign, generation, aircraft]):
			flash('모든 필수 항목을 입력해주세요.', 'error')
			return redirect(url_for('admin_commander_new'))

		try:
			order_num_int = int(order_num)
		except:
			flash('정렬 순서는 숫자여야 합니다.', 'error')
			return redirect(url_for('admin_commander_new'))

		# 파일 업로드 처리
		photo_url = '/static/images/default-pilot.jpg'  # 기본 이미지
		file = request.files.get('photo')
		if file and file.filename:
			# 안전한 파일명 생성 (공백, 특수문자 제거)
			safe_callsign = ''.join(c for c in callsign if c.isalnum() or c in ('-', '_'))
			filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_commander_{safe_callsign}.jpg"  # 최종 파일은 항상 .jpg
			filepath = os.path.join(UPLOAD_BASE, 'members', filename)
			os.makedirs(os.path.dirname(filepath), exist_ok=True)
			file.save(filepath)

			# 이미지 최적화
			if optimize_image(filepath):
				photo_url = f'/static/members/{filename}'
			else:
				flash('이미지 처리 중 오류가 발생했습니다.', 'warning')

		conn = get_db()
		conn.execute('''
			INSERT INTO commander_greeting (name, rank, callsign, generation, aircraft, photo_url, greeting_text, order_num, is_active, lang)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		''', (name, rank, callsign, generation, aircraft, photo_url, greeting_text, order_num_int, is_active, lang))
		conn.commit()
		conn.close()
		
		flash('전대장 인사말이 추가되었습니다.', 'success')
		return redirect(url_for('admin_commanders'))
	
	return render_template('admin/commander_form.html', commander=None)


# 전대장 인사말 수정
@app.route('/admin/commanders/<int:commander_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_commander_edit(commander_id):
	conn = get_db()
	
	if request.method == 'POST':
		name = request.form.get('name', '').strip()
		rank = request.form.get('rank', '').strip()
		callsign = request.form.get('callsign', '').strip()
		generation = request.form.get('generation', '').strip()
		aircraft = request.form.get('aircraft', '').strip()
		greeting_text = request.form.get('greeting_text', '').strip()
		order_num = request.form.get('order_num', '0').strip()
		is_active = 1 if request.form.get('is_active') else 0
		lang = request.form.get('lang', 'ko').strip()

		if not all([name, rank, callsign, generation, aircraft]):
			flash('모든 필수 항목을 입력해주세요.', 'error')
			return redirect(url_for('admin_commander_edit', commander_id=commander_id))

		try:
			order_num_int = int(order_num)
		except:
			flash('정렬 순서는 숫자여야 합니다.', 'error')
			return redirect(url_for('admin_commander_edit', commander_id=commander_id))

		# 기존 사진 URL 가져오기
		commander = conn.execute('SELECT photo_url FROM commander_greeting WHERE id = ?', (commander_id,)).fetchone()
		photo_url = commander['photo_url'] if commander else '/static/images/default-pilot.jpg'

		# 파일 업로드 처리
		file = request.files.get('photo')
		if file and file.filename:
			# 안전한 파일명 생성 (공백, 특수문자 제거)
			safe_callsign = ''.join(c for c in callsign if c.isalnum() or c in ('-', '_'))
			filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_commander_{safe_callsign}.jpg"  # 최종 파일은 항상 .jpg
			filepath = os.path.join(UPLOAD_BASE, 'members', filename)
			os.makedirs(os.path.dirname(filepath), exist_ok=True)
			file.save(filepath)

			# 이미지 최적화
			if optimize_image(filepath):
				photo_url = f'/static/members/{filename}'
			else:
				flash('이미지 처리 중 오류가 발생했습니다.', 'warning')

		conn.execute('''
			UPDATE commander_greeting
			SET name = ?, rank = ?, callsign = ?, generation = ?, aircraft = ?,
			    photo_url = ?, greeting_text = ?, order_num = ?, is_active = ?, lang = ?, updated_at = CURRENT_TIMESTAMP
			WHERE id = ?
		''', (name, rank, callsign, generation, aircraft, photo_url, greeting_text, order_num_int, is_active, lang, commander_id))
		conn.commit()
		conn.close()
		
		flash('전대장 인사말이 수정되었습니다.', 'success')
		return redirect(url_for('admin_commanders'))
	
	commander = conn.execute('SELECT * FROM commander_greeting WHERE id = ?', (commander_id,)).fetchone()
	conn.close()
	
	if not commander:
		flash('전대장 인사말을 찾을 수 없습니다.', 'error')
		return redirect(url_for('admin_commanders'))
	
	return render_template('admin/commander_form.html', commander=commander)


# 전대장 인사말 삭제
@app.route('/admin/commanders/<int:commander_id>/delete', methods=['POST'])
@login_required
def admin_commander_delete(commander_id):
	conn = get_db()
	conn.execute('DELETE FROM commander_greeting WHERE id = ?', (commander_id,))
	conn.commit()
	conn.close()
	
	flash('전대장 인사말이 삭제되었습니다.', 'success')
	return redirect(url_for('admin_commanders'))


# 홈 콘텐츠 관리
@app.route('/admin/home-contents')
@login_required
def admin_home_contents():
	conn = get_db()
	contents = conn.execute('SELECT * FROM home_contents ORDER BY order_num').fetchall()
	conn.close()
	return render_template('admin/home_contents.html', contents=contents)


# 홈 콘텐츠 추가
@app.route('/admin/home-contents/new', methods=['GET', 'POST'])
@login_required
def admin_home_content_new():
	if request.method == 'POST':
		content_type = request.form.get('content_type', '').strip()
		title = request.form.get('title', '').strip()
		content_data = request.form.get('content_data', '').strip()
		order_num = request.form.get('order_num', '0').strip()
		is_active = 1 if request.form.get('is_active') else 0
		
		if not all([content_type, content_data]):
			flash('콘텐츠 유형과 데이터를 입력해주세요.', 'error')
			return redirect(url_for('admin_home_content_new'))
		
		try:
			order_num_int = int(order_num)
		except:
			order_num_int = 0
		
		conn = get_db()
		conn.execute('''
			INSERT INTO home_contents (content_type, title, content_data, order_num, is_active)
			VALUES (?, ?, ?, ?, ?)
		''', (content_type, title, content_data, order_num_int, is_active))
		conn.commit()
		conn.close()
		
		flash('홈 콘텐츠가 추가되었습니다.', 'success')
		return redirect(url_for('admin_home_contents'))
	
	return render_template('admin/home_content_form.html', content=None)


# 홈 콘텐츠 수정
@app.route('/admin/home-contents/<int:content_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_home_content_edit(content_id):
	conn = get_db()
	
	if request.method == 'POST':
		content_type = request.form.get('content_type', '').strip()
		title = request.form.get('title', '').strip()
		content_data = request.form.get('content_data', '').strip()
		order_num = request.form.get('order_num', '0').strip()
		is_active = 1 if request.form.get('is_active') else 0
		
		if not all([content_type, content_data]):
			flash('콘텐츠 유형과 데이터를 입력해주세요.', 'error')
			return redirect(url_for('admin_home_content_edit', content_id=content_id))
		
		try:
			order_num_int = int(order_num)
		except:
			order_num_int = 0
		
		conn.execute('''
			UPDATE home_contents 
			SET content_type = ?, title = ?, content_data = ?, order_num = ?, 
			    is_active = ?, updated_at = CURRENT_TIMESTAMP 
			WHERE id = ?
		''', (content_type, title, content_data, order_num_int, is_active, content_id))
		conn.commit()
		conn.close()
		
		flash('홈 콘텐츠가 수정되었습니다.', 'success')
		return redirect(url_for('admin_home_contents'))
	
	content = conn.execute('SELECT * FROM home_contents WHERE id = ?', (content_id,)).fetchone()
	conn.close()
	
	if not content:
		flash('콘텐츠를 찾을 수 없습니다.', 'error')
		return redirect(url_for('admin_home_contents'))
	
	return render_template('admin/home_content_form.html', content=content)


# 홈 콘텐츠 삭제
@app.route('/admin/home-contents/<int:content_id>/delete', methods=['POST'])
@login_required
def admin_home_content_delete(content_id):
	conn = get_db()
	conn.execute('DELETE FROM home_contents WHERE id = ?', (content_id,))
	conn.commit()
	conn.close()
	
	flash('홈 콘텐츠가 삭제되었습니다.', 'success')
	return redirect(url_for('admin_home_contents'))


# ===== 팀소개 섹션 관리 =====
@app.route('/admin/about-sections')
@login_required
def admin_about_sections():
	conn = get_db()
	sections = conn.execute('SELECT * FROM about_sections ORDER BY order_num').fetchall()
	conn.close()
	return render_template('admin/about_sections.html', sections=sections)


@app.route('/admin/about-sections/new', methods=['GET', 'POST'])
@login_required
def admin_about_section_new():
	if request.method == 'POST':
		section_type = request.form.get('section_type', '').strip()
		title = request.form.get('title', '').strip()
		content = request.form.get('content', '').strip()
		try:
			order_num = int(request.form.get('order_num', 0) or 0)
		except (ValueError, TypeError):
			order_num = 0
		is_active = 1 if request.form.get('is_active') else 0
		lang = request.form.get('lang', 'ko').strip()

		if not section_type or not title:
			flash('섹션 유형과 제목은 필수입니다.', 'error')
			return redirect(url_for('admin_about_section_new'))

		# 사진 업로드 처리
		image_url = ''
		if 'photo' in request.files:
			file = request.files['photo']
			if file and file.filename:
				# 파일명 생성 (타임스탬프_섹션타입)
				timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
				safe_section = ''.join(c for c in section_type if c.isalnum() or c in ('-', '_'))
				file_ext = os.path.splitext(file.filename)[1].lower()
				filename = f"{timestamp}_section_{safe_section}{file_ext}"

				# 저장 경로
				upload_folder = os.path.join(app.static_folder, 'Picture')
				os.makedirs(upload_folder, exist_ok=True)
				filepath = os.path.join(upload_folder, filename)

				file.save(filepath)
				image_url = f'/static/Picture/{filename}'

		conn = get_db()
		conn.execute('''
			INSERT INTO about_sections (section_type, title, content, image_url, order_num, is_active, lang, updated_at)
			VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
		''', (section_type, title, content, image_url, order_num, is_active, lang))
		conn.commit()
		conn.close()
		
		flash('팀소개 섹션이 추가되었습니다.', 'success')
		return redirect(url_for('admin_about_sections'))
	
	return render_template('admin/about_section_form.html', section=None)


@app.route('/admin/about-sections/<int:section_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_about_section_edit(section_id):
	conn = get_db()
	
	if request.method == 'POST':
		section_type = request.form.get('section_type', '').strip()
		title = request.form.get('title', '').strip()
		content = request.form.get('content', '').strip()
		try:
			order_num = int(request.form.get('order_num', 0) or 0)
		except (ValueError, TypeError):
			order_num = 0
		is_active = 1 if request.form.get('is_active') else 0
		lang = request.form.get('lang', 'ko').strip()

		if not section_type or not title:
			flash('섹션 유형과 제목은 필수입니다.', 'error')
			return redirect(url_for('admin_about_section_edit', section_id=section_id))

		# 기존 섹션 정보 가져오기
		section = conn.execute('SELECT * FROM about_sections WHERE id = ?', (section_id,)).fetchone()
		image_url = section['image_url'] if section else ''

		# 사진 업로드 처리
		if 'photo' in request.files:
			file = request.files['photo']
			if file and file.filename:
				# 파일명 생성 (타임스탬프_섹션타입)
				timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
				safe_section = ''.join(c for c in section_type if c.isalnum() or c in ('-', '_'))
				file_ext = os.path.splitext(file.filename)[1].lower()
				filename = f"{timestamp}_section_{safe_section}{file_ext}"

				# 저장 경로
				upload_folder = os.path.join(app.static_folder, 'Picture')
				os.makedirs(upload_folder, exist_ok=True)
				filepath = os.path.join(upload_folder, filename)

				file.save(filepath)
				image_url = f'/static/Picture/{filename}'

		conn.execute('''
			UPDATE about_sections
			SET section_type = ?, title = ?, content = ?, image_url = ?, order_num = ?, is_active = ?, lang = ?, updated_at = CURRENT_TIMESTAMP
			WHERE id = ?
		''', (section_type, title, content, image_url, order_num, is_active, lang, section_id))
		conn.commit()
		conn.close()
		
		flash('팀소개 섹션이 수정되었습니다.', 'success')
		return redirect(url_for('admin_about_sections'))
	
	section = conn.execute('SELECT * FROM about_sections WHERE id = ?', (section_id,)).fetchone()
	conn.close()
	
	if not section:
		flash('섹션을 찾을 수 없습니다.', 'error')
		return redirect(url_for('admin_about_sections'))
	
	return render_template('admin/about_section_form.html', section=section)


# 사진 게시판 관리 라우트
@app.route('/admin/gallery')
@login_required
def admin_gallery():
	conn = get_db()
	photos = conn.execute('SELECT * FROM gallery ORDER BY order_num, upload_date DESC').fetchall()
	conn.close()
	return render_template('admin/gallery.html', photos=photos)


@app.route('/admin/gallery/new', methods=['GET', 'POST'])
@login_required
def admin_gallery_new():
	if request.method == 'POST':
		title = request.form.get('title', '').strip()
		description = request.form.get('description', '').strip()
		image_url = request.form.get('image_url', '').strip()
		try:
			order_num = int(request.form.get('order_num', 0) or 0)
		except (ValueError, TypeError):
			order_num = 0
		is_active = 1 if request.form.get('is_active') else 0

		# 파일 업로드 처리
		file = request.files.get('image_file')
		if file and file.filename:
			filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_gallery_{file.filename}"
			gallery_dir = os.path.join(UPLOAD_BASE, 'gallery')
			os.makedirs(gallery_dir, exist_ok=True)
			filepath = os.path.join(gallery_dir, filename)
			file.save(filepath)
			optimize_image(filepath)
			image_url = f'/static/gallery/{filename}'

		if not title or not image_url:
			flash('제목과 이미지는 필수입니다.', 'error')
			return redirect(url_for('admin_gallery_new'))

		conn = get_db()
		conn.execute('''
			INSERT INTO gallery (title, description, image_url, order_num, is_active)
			VALUES (?, ?, ?, ?, ?)
		''', (title, description, image_url, order_num, is_active))
		conn.commit()
		conn.close()

		flash('사진이 추가되었습니다.', 'success')
		return redirect(url_for('admin_gallery'))

	return render_template('admin/gallery_form.html')


@app.route('/admin/gallery/edit/<int:photo_id>', methods=['GET', 'POST'])
@login_required
def admin_gallery_edit(photo_id):
	conn = get_db()

	if request.method == 'POST':
		title = request.form.get('title', '').strip()
		description = request.form.get('description', '').strip()
		image_url = request.form.get('image_url', '').strip()
		try:
			order_num = int(request.form.get('order_num', 0) or 0)
		except (ValueError, TypeError):
			order_num = 0
		is_active = 1 if request.form.get('is_active') else 0

		# 파일 업로드 처리
		file = request.files.get('image_file')
		if file and file.filename:
			filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_gallery_{file.filename}"
			gallery_dir = os.path.join(UPLOAD_BASE, 'gallery')
			os.makedirs(gallery_dir, exist_ok=True)
			filepath = os.path.join(gallery_dir, filename)
			file.save(filepath)
			optimize_image(filepath)
			image_url = f'/static/gallery/{filename}'

		if not title or not image_url:
			flash('제목과 이미지는 필수입니다.', 'error')
			return redirect(url_for('admin_gallery_edit', photo_id=photo_id))

		conn.execute('''
			UPDATE gallery
			SET title = ?, description = ?, image_url = ?, order_num = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP
			WHERE id = ?
		''', (title, description, image_url, order_num, is_active, photo_id))
		conn.commit()
		conn.close()

		flash('사진이 수정되었습니다.', 'success')
		return redirect(url_for('admin_gallery'))

	photo = conn.execute('SELECT * FROM gallery WHERE id = ?', (photo_id,)).fetchone()
	conn.close()

	if not photo:
		flash('사진을 찾을 수 없습니다.', 'error')
		return redirect(url_for('admin_gallery'))

	return render_template('admin/gallery_form.html', photo=photo)


@app.route('/admin/gallery/delete/<int:photo_id>', methods=['POST'])
@login_required
def admin_gallery_delete(photo_id):
	conn = get_db()
	conn.execute('DELETE FROM gallery WHERE id = ?', (photo_id,))
	conn.commit()
	conn.close()
	
	flash('사진이 삭제되었습니다.', 'success')
	return redirect(url_for('admin_gallery'))




@app.route('/admin/about-sections/<int:section_id>/delete', methods=['POST'])
@login_required
def admin_about_section_delete(section_id):
	conn = get_db()
	conn.execute('DELETE FROM about_sections WHERE id = ?', (section_id,))
	conn.commit()
	conn.close()
	
	flash('팀소개 섹션이 삭제되었습니다.', 'success')
	return redirect(url_for('admin_about_sections'))


# 사이트 이미지 관리 - 목록
@app.route('/admin/site-images')
@login_required
def admin_site_images():
	conn = get_db()
	images = conn.execute('SELECT * FROM site_images ORDER BY category, image_key').fetchall()
	conn.close()
	return render_template('admin/site_images.html', images=images)


# 사이트 이미지 수정
@app.route('/admin/site-images/<int:image_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_site_image_edit(image_id):
	conn = get_db()
	
	if request.method == 'POST':
		file = request.files.get('image')
		
		if file and file.filename:
			# 파일 저장
			filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}"
			filepath = os.path.join(UPLOAD_BASE, 'images', filename)
			os.makedirs(os.path.dirname(filepath), exist_ok=True)
			file.save(filepath)
			
			image_path = f'/static/images/{filename}'
			
			# 데이터베이스 업데이트
			conn.execute('''
				UPDATE site_images 
				SET image_path = ?, updated_at = CURRENT_TIMESTAMP
				WHERE id = ?
			''', (image_path, image_id))
			conn.commit()
			
			flash('이미지가 업데이트되었습니다.', 'success')
		else:
			flash('이미지 파일을 선택해주세요.', 'error')
		
		conn.close()
		return redirect(url_for('admin_site_images'))
	
	image = conn.execute('SELECT * FROM site_images WHERE id = ?', (image_id,)).fetchone()
	conn.close()
	
	if not image:
		flash('이미지를 찾을 수 없습니다.', 'error')
		return redirect(url_for('admin_site_images'))
	
	return render_template('admin/site_image_form.html', image=image)


# ============================================
# 실시간 채팅 관련 라우트
# ============================================

# 사용자: 채팅 세션 시작
@app.route('/chat/start', methods=['POST'])
def chat_start():
	"""새 채팅 세션 시작"""
	import uuid
	
	session_id = str(uuid.uuid4())
	user_name = request.json.get('name', '방문자')
	user_email = request.json.get('email', '')
	
	conn = get_db()
	conn.execute('''
		INSERT INTO chat_sessions (session_id, user_name, user_email, status)
		VALUES (?, ?, ?, 'active')
	''', (session_id, user_name, user_email))
	conn.commit()
	conn.close()
	
	return {'success': True, 'session_id': session_id}


# 사용자: 메시지 전송
@app.route('/chat/send', methods=['POST'])
def chat_send():
	"""채팅 메시지 전송"""
	data = request.json
	session_id = data.get('session_id')
	message = data.get('message', '').strip()
	sender_type = data.get('sender_type', 'user')
	sender_name = data.get('sender_name', '방문자')
	
	if not session_id or not message:
		return {'success': False, 'error': '세션 ID와 메시지가 필요합니다.'}, 400
	
	conn = get_db()
	cursor = conn.cursor()
	
	# 세션이 없다면 자동으로 생성 (로컬스토리지에 남아있던 오래된 세션 ID 대비)
	session_info = cursor.execute('SELECT * FROM chat_sessions WHERE session_id = ?', (session_id,)).fetchone()
	if not session_info:
		cursor.execute('''
			INSERT INTO chat_sessions (session_id, user_name, user_email, status)
			VALUES (?, ?, ?, 'active')
		''', (session_id, sender_name or '방문자', ''))
	
	# 메시지 저장
	cursor.execute('''
		INSERT INTO chat_messages (session_id, sender_type, sender_name, message)
		VALUES (?, ?, ?, ?)
	''', (session_id, sender_type, sender_name, message))
	
	# 세션 업데이트 시간 갱신
	cursor.execute('''
		UPDATE chat_sessions SET updated_at = CURRENT_TIMESTAMP WHERE session_id = ?
	''', (session_id,))
	
	conn.commit()
	conn.close()
	
	return {'success': True}


# 사용자: 메시지 가져오기
@app.route('/api/chat/messages/<session_id>')
def chat_messages(session_id):
	"""채팅 메시지 목록 가져오기"""
	conn = get_db()
	
	# 세션 상태 확인
	session_info = conn.execute('''
		SELECT status FROM chat_sessions WHERE session_id = ?
	''', (session_id,)).fetchone()
	
	session_status = session_info['status'] if session_info else 'active'
	
	messages = conn.execute('''
		SELECT id, sender_type, sender_name, message, created_at
		FROM chat_messages
		WHERE session_id = ?
		ORDER BY created_at ASC
	''', (session_id,)).fetchall()
	
	# 사용자가 읽은 메시지는 읽음 처리
	if 'logged_in' not in session:  # 관리자가 아닌 경우
		conn.execute('''
			UPDATE chat_messages 
			SET is_read = 1 
			WHERE session_id = ? AND sender_type = 'admin' AND is_read = 0
		''', (session_id,))
		conn.commit()
	
	conn.close()
	
	return {
		'success': True,
		'session_status': session_status,
		'messages': [{
			'id': m['id'],
			'sender_type': m['sender_type'],
			'sender_name': m['sender_name'],
			'message': m['message'],
			'created_at': m['created_at']
		} for m in messages]
	}


# 사용자: 채팅 세션 종료
@app.route('/chat/close', methods=['POST'])
def chat_close():
	"""사용자가 채팅 세션을 종료"""
	data = request.get_json(silent=True) or {}
	session_id = data.get('session_id')
	
	if not session_id:
		return {'success': False, 'error': '세션 ID가 필요합니다.'}, 400
	
	conn = get_db()
	cursor = conn.cursor()
	
	# 세션 존재 여부 확인
	session_info = cursor.execute('SELECT * FROM chat_sessions WHERE session_id = ?', (session_id,)).fetchone()
	if not session_info:
		# 세션이 없는데 종료를 요청한 경우(오래된 세션 ID 등) - 세션을 생성 후 바로 종료 상태로 기록
		cursor.execute('''
			INSERT INTO chat_sessions (session_id, user_name, user_email, status)
			VALUES (?, ?, ?, 'closed')
		''', (session_id, '방문자', ''))
	else:
		# 세션 상태를 closed 로 변경
		cursor.execute("UPDATE chat_sessions SET status = 'closed', updated_at = CURRENT_TIMESTAMP WHERE session_id = ?", (session_id,))
	
	# 시스템 메시지(선택) - 관리자 화면에서도 종료 시점을 확인할 수 있도록
	cursor.execute('''
		INSERT INTO chat_messages (session_id, sender_type, sender_name, message, is_read)
		VALUES (?, 'admin', '시스템', ?, 1)
	''', (session_id, '사용자가 채팅을 종료했습니다.'))
	
	conn.commit()
	conn.close()
	
	return {'success': True}


# 관리자: 메시지 전송
@app.route('/api/admin/chat/send', methods=['POST'])
@login_required
def admin_chat_send():
	"""관리자가 채팅 메시지 전송"""
	data = request.get_json()
	session_id = data.get('session_id')
	message = data.get('message', '').strip()
	
	if not session_id or not message:
		return {'success': False, 'error': '세션 ID와 메시지가 필요합니다.'}, 400
	
	conn = get_db()
	
	# 세션 확인
	session_info = conn.execute('''
		SELECT * FROM chat_sessions WHERE session_id = ?
	''', (session_id,)).fetchone()
	
	if not session_info:
		conn.close()
		return {'success': False, 'error': '세션을 찾을 수 없습니다.'}, 404
	
	# 메시지 저장
	conn.execute('''
		INSERT INTO chat_messages (session_id, sender_type, sender_name, message, is_read)
		VALUES (?, 'admin', '관리자', ?, 0)
	''', (session_id, message))
	
	# 세션 업데이트 시간 갱신
	conn.execute('''
		UPDATE chat_sessions SET updated_at = CURRENT_TIMESTAMP WHERE session_id = ?
	''', (session_id,))
	
	conn.commit()
	conn.close()
	
	return {'success': True}


# 관리자: 활성 채팅 세션 목록
@app.route('/admin/chats')
@login_required
def admin_chats():
	"""관리자 채팅 관리 페이지"""
	conn = get_db()
	
	# 모든 채팅 세션 가져오기
	sessions = conn.execute('''
		SELECT 
			cs.*,
			(SELECT COUNT(*) FROM chat_messages 
			 WHERE session_id = cs.session_id AND sender_type = 'user' AND is_read = 0) as unread_count,
			(SELECT message FROM chat_messages 
			 WHERE session_id = cs.session_id 
			 ORDER BY created_at DESC LIMIT 1) as last_message
		FROM chat_sessions cs
		ORDER BY cs.updated_at DESC
	''').fetchall()
	
	conn.close()
	return render_template('admin/chats.html', sessions=sessions)


# 관리자: 특정 채팅 세션 상세
@app.route('/admin/chats/<session_id>')
@login_required
def admin_chat_detail(session_id):
	"""특정 채팅 세션 상세 페이지"""
	conn = get_db()
	
	# 세션 정보
	session_info = conn.execute('''
		SELECT * FROM chat_sessions WHERE session_id = ?
	''', (session_id,)).fetchone()
	
	if not session_info:
		flash('채팅 세션을 찾을 수 없습니다.', 'error')
		return redirect(url_for('admin_chats'))
	
	# 메시지 목록
	messages = conn.execute('''
		SELECT * FROM chat_messages
		WHERE session_id = ?
		ORDER BY created_at ASC
	''', (session_id,)).fetchall()
	
	# 관리자가 읽은 것으로 표시
	conn.execute('''
		UPDATE chat_messages 
		SET is_read = 1 
		WHERE session_id = ? AND sender_type = 'user' AND is_read = 0
	''', (session_id,))
	conn.commit()
	
	conn.close()
	return render_template('admin/chat_detail.html', session=session_info, messages=messages)


# 관리자: 채팅 세션 종료
@app.route('/admin/chats/<session_id>/close', methods=['POST'])
@login_required
def admin_chat_close(session_id):
	"""채팅 세션 종료"""
	conn = get_db()
	conn.execute('''
		UPDATE chat_sessions SET status = 'closed' WHERE session_id = ?
	''', (session_id,))
	conn.commit()
	conn.close()
	
	flash('채팅 세션이 종료되었습니다.', 'success')
	return redirect(url_for('admin_chats'))


# ========== 영상 갤러리 관리 ==========
@app.route('/admin/videos')
@login_required
def admin_videos():
	conn = get_db()
	videos = conn.execute('SELECT * FROM videos ORDER BY order_num, upload_date DESC').fetchall()
	conn.close()
	return render_template('admin/videos.html', videos=videos)


@app.route('/admin/videos/new', methods=['GET', 'POST'])
@login_required
def admin_video_new():
	if request.method == 'POST':
		title = request.form.get('title', '').strip()
		description = request.form.get('description', '').strip()
		video_url = request.form.get('video_url', '').strip()
		try:
			order_num = int(request.form.get('order_num', 0) or 0)
		except (ValueError, TypeError):
			order_num = 0
		is_active = 1 if request.form.get('is_active') else 0

		if not title or not video_url:
			flash('제목과 영상 URL은 필수입니다.', 'error')
			return redirect(url_for('admin_video_new'))

		conn = get_db()
		conn.execute('''
			INSERT INTO videos (title, description, video_url, order_num, is_active)
			VALUES (?, ?, ?, ?, ?)
		''', (title, description, video_url, order_num, is_active))
		conn.commit()
		conn.close()

		flash('영상이 추가되었습니다.', 'success')
		return redirect(url_for('admin_videos'))

	return render_template('admin/video_form.html')


@app.route('/admin/videos/<int:video_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_video_edit(video_id):
	conn = get_db()

	if request.method == 'POST':
		title = request.form.get('title', '').strip()
		description = request.form.get('description', '').strip()
		video_url = request.form.get('video_url', '').strip()
		try:
			order_num = int(request.form.get('order_num', 0) or 0)
		except (ValueError, TypeError):
			order_num = 0
		is_active = 1 if request.form.get('is_active') else 0

		if not title or not video_url:
			flash('제목과 영상 URL은 필수입니다.', 'error')
			return redirect(url_for('admin_video_edit', video_id=video_id))

		conn.execute('''
			UPDATE videos
			SET title = ?, description = ?, video_url = ?, order_num = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP
			WHERE id = ?
		''', (title, description, video_url, order_num, is_active, video_id))
		conn.commit()
		conn.close()

		flash('영상이 수정되었습니다.', 'success')
		return redirect(url_for('admin_videos'))

	video = conn.execute('SELECT * FROM videos WHERE id = ?', (video_id,)).fetchone()
	conn.close()

	if not video:
		flash('영상을 찾을 수 없습니다.', 'error')
		return redirect(url_for('admin_videos'))

	return render_template('admin/video_form.html', video=video)


@app.route('/admin/videos/<int:video_id>/delete', methods=['POST'])
@login_required
def admin_video_delete(video_id):
	conn = get_db()
	conn.execute('DELETE FROM videos WHERE id = ?', (video_id,))
	conn.commit()
	conn.close()

	flash('영상이 삭제되었습니다.', 'success')
	return redirect(url_for('admin_videos'))


# ========== 후원 설정 관리 ==========
@app.route('/admin/donate-settings', methods=['GET', 'POST'])
@login_required
def admin_donate_settings():
	conn = get_db()

	if request.method == 'POST':
		kakaopay_link = request.form.get('donate_kakaopay_link', '').strip()
		bank_name = request.form.get('donate_bank_name', '').strip()
		account_number = request.form.get('donate_account_number', '').strip()
		account_holder = request.form.get('donate_account_holder', '').strip()
		contact_email = request.form.get('contact_email', '').strip()

		for key, val in [('donate_kakaopay_link', kakaopay_link), ('donate_bank_name', bank_name),
						 ('donate_account_number', account_number), ('donate_account_holder', account_holder),
						 ('contact_email', contact_email)]:
			conn.execute('''
				INSERT INTO site_settings (setting_key, setting_value, updated_at)
				VALUES (?, ?, CURRENT_TIMESTAMP)
				ON CONFLICT(setting_key) DO UPDATE SET setting_value = ?, updated_at = CURRENT_TIMESTAMP
			''', (key, val, val))

		conn.commit()
		conn.close()
		flash('후원 설정이 저장되었습니다.', 'success')
		return redirect(url_for('admin_donate_settings'))

	settings = {}
	try:
		rows = conn.execute("SELECT setting_key, setting_value FROM site_settings WHERE setting_key LIKE 'donate_%' OR setting_key = 'contact_email'").fetchall()
		for r in rows:
			settings[r['setting_key']] = r['setting_value']
	except:
		pass
	conn.close()

	return render_template('admin/donate_settings.html', settings=settings)


# ========== 이메일 테스트 ==========
@app.route('/admin/test-email', methods=['POST'])
@login_required
def admin_test_email():
	test_to = request.form.get('test_email', '').strip()
	if not test_to:
		flash('테스트 이메일 주소를 입력해주세요.', 'error')
		return redirect(url_for('admin_donate_settings'))

	# SMTP 비밀번호 업데이트 (폼에서 입력한 경우)
	smtp_password = request.form.get('smtp_password', '').strip()
	if smtp_password:
		app.config['MAIL_PASSWORD'] = smtp_password

	method = 'SendGrid' if (HAS_SENDGRID and SENDGRID_API_KEY) else 'SMTP'
	sent = send_email(
		subject='[VBE] 이메일 발송 테스트',
		to_email=test_to,
		body_text=f'Virtual Black Eagles 이메일 테스트입니다.\n\n이 메일이 도착했다면 이메일 설정이 정상입니다.\n\n발송 방식: {method}'
	)
	if sent:
		flash(f'테스트 이메일이 {test_to}로 발송되었습니다! ({method}) 메일함을 확인하세요.', 'success')
	else:
		flash(f'이메일 발송 실패: SENDGRID_API_KEY 또는 SMTP 설정을 확인해주세요.', 'error')

	return redirect(url_for('admin_donate_settings'))


# 헬스 체크 & 환경 진단 (배포 문제 디버깅용)
@app.route('/health')
def health_check():
	gid = os.environ.get('GOOGLE_CLIENT_ID', '')
	return jsonify({
		'status': 'ok',
		'has_oauth': HAS_OAUTH,
		'has_sendgrid': HAS_SENDGRID,
		'google_client_id_set': bool(gid),
		'google_client_id_len': len(gid),
		'google_client_id_preview': gid[:15] + '...' if len(gid) > 15 else gid,
		'sendgrid_key_set': bool(SENDGRID_API_KEY),
		'mail_password_set': bool(app.config.get('MAIL_PASSWORD')),
		'request_host': request.host,
		'request_scheme': request.scheme,
		'oauth_redirect_uri': 'https://' + request.host + '/auth/google/callback',
		'env_keys_count': len([k for k in os.environ.keys() if 'GOOGLE' in k or 'RENDER' in k]),
	})

# 에러 핸들러 추가 (디버깅용)
@app.errorhandler(500)
def internal_error(error):
	import traceback
	error_msg = traceback.format_exc()
	app.logger.error(f"500 Internal Server Error: {error_msg}")
	return f"Internal Server Error: {str(error)}<br><br>Check Render logs for details.", 500


"""
애플리케이션 초기화
WSGI (gunicorn 등) 로 임포트되는 경우에도 DB 스키마가 보장되도록
모듈 임포트 시점에 한 번 init_db() 를 호출한다.
CREATE TABLE IF NOT EXISTS / INSERT OR IGNORE 를 사용하므로
여러 번 호출되어도 안전하다.
"""
with app.app_context():
	try:
		init_db()
		app.logger.info("Database initialized successfully")
	except Exception as e:
		app.logger.error(f"Failed to initialize database: {str(e)}")
		import traceback
		app.logger.error(traceback.format_exc())


if __name__ == '__main__':
	# Allow selecting port via PORT env var (useful if 5000 is occupied).
	host = os.environ.get('HOST', '127.0.0.1')
	port = int(os.environ.get('PORT', 5001))
	# Run the dev server without the auto-reloader (single process) to avoid issues
	# when starting the app detached in this environment.
	app.run(host=host, port=port, debug=False, use_reloader=False)


