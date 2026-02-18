#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
비행 스케줄 관리 프로그램
Virtual Black Eagles 팀의 비행 일정을 관리합니다.
"""

import sqlite3
from datetime import datetime, timedelta
import sys
from tabulate import tabulate


class FlightScheduleManager:
    """비행 스케줄 관리 클래스"""
    
    def __init__(self, db_path='flight_schedules.db'):
        """초기화 - 독립 데이터베이스 사용"""
        self.db_path = db_path
        self.conn = None
        self.cursor = None
        self._initialize_database()
    
    def connect(self):
        """데이터베이스 연결"""
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
            self.cursor = self.conn.cursor()
            return True
        except sqlite3.Error as e:
            print(f"❌ 데이터베이스 연결 실패: {e}")
            return False
    
    def close(self):
        """데이터베이스 연결 종료"""
        if self.conn:
            self.conn.close()
    
    def _initialize_database(self):
        """데이터베이스 초기화 - 테이블 생성"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # schedules 테이블 생성
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    location TEXT,
                    event_date TEXT NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            conn.close()
            
        except sqlite3.Error as e:
            print(f"❌ 데이터베이스 초기화 실패: {e}")
            sys.exit(1)
    
    def add_schedule(self, title, location, event_date, description=''):
        """새로운 일정 추가"""
        try:
            # 날짜 형식 검증
            try:
                datetime.strptime(event_date, '%Y-%m-%d')
            except ValueError:
                print("❌ 날짜 형식 오류. YYYY-MM-DD 형식으로 입력하세요.")
                return False
            
            self.cursor.execute('''
                INSERT INTO schedules (title, location, event_date, description)
                VALUES (?, ?, ?, ?)
            ''', (title, location, event_date, description))
            self.conn.commit()
            print(f"✅ 일정이 추가되었습니다: {title}")
            return True
        except sqlite3.Error as e:
            print(f"❌ 일정 추가 실패: {e}")
            return False
    
    def list_schedules(self, filter_type='all', days=30):
        """일정 목록 조회
        
        Args:
            filter_type: 'all' (전체), 'upcoming' (다가오는), 'past' (지난), 'today' (오늘)
            days: upcoming 조회 시 기준 일수
        """
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            future = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')
            
            if filter_type == 'upcoming':
                query = 'SELECT * FROM schedules WHERE event_date >= ? ORDER BY event_date ASC'
                self.cursor.execute(query, (today,))
            elif filter_type == 'past':
                query = 'SELECT * FROM schedules WHERE event_date < ? ORDER BY event_date DESC'
                self.cursor.execute(query, (today,))
            elif filter_type == 'today':
                query = 'SELECT * FROM schedules WHERE event_date = ? ORDER BY event_date ASC'
                self.cursor.execute(query, (today,))
            elif filter_type == 'week':
                week_future = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')
                query = 'SELECT * FROM schedules WHERE event_date >= ? AND event_date <= ? ORDER BY event_date ASC'
                self.cursor.execute(query, (today, week_future))
            elif filter_type == 'month':
                month_future = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
                query = 'SELECT * FROM schedules WHERE event_date >= ? AND event_date <= ? ORDER BY event_date ASC'
                self.cursor.execute(query, (today, month_future))
            else:  # all
                query = 'SELECT * FROM schedules ORDER BY event_date DESC'
                self.cursor.execute(query)
            
            schedules = self.cursor.fetchall()
            
            if not schedules:
                print("📭 등록된 일정이 없습니다.")
                return []
            
            # 테이블 형식으로 출력
            table_data = []
            for schedule in schedules:
                # D-Day 계산
                event_date = datetime.strptime(schedule['event_date'], '%Y-%m-%d')
                today_date = datetime.now()
                delta = (event_date.date() - today_date.date()).days
                
                if delta < 0:
                    d_day = f"D+{abs(delta)}"
                elif delta == 0:
                    d_day = "D-Day"
                else:
                    d_day = f"D-{delta}"
                
                table_data.append([
                    schedule['id'],
                    schedule['title'],
                    schedule['location'] or '-',
                    schedule['event_date'],
                    d_day,
                    schedule['description'][:30] + '...' if schedule['description'] and len(schedule['description']) > 30 else schedule['description'] or '-'
                ])
            
            headers = ['ID', '제목', '장소', '날짜', 'D-Day', '설명']
            print("\n" + "="*100)
            print(tabulate(table_data, headers=headers, tablefmt='grid'))
            print("="*100 + "\n")
            
            return schedules
        except sqlite3.Error as e:
            print(f"❌ 일정 조회 실패: {e}")
            return []
    
    def get_schedule(self, schedule_id):
        """특정 일정 상세 조회"""
        try:
            self.cursor.execute('SELECT * FROM schedules WHERE id = ?', (schedule_id,))
            schedule = self.cursor.fetchone()
            
            if not schedule:
                print(f"❌ ID {schedule_id}번 일정을 찾을 수 없습니다.")
                return None
            
            # 상세 정보 출력
            print("\n" + "="*60)
            print(f"📋 일정 상세 정보")
            print("="*60)
            print(f"ID: {schedule['id']}")
            print(f"제목: {schedule['title']}")
            print(f"장소: {schedule['location'] or '-'}")
            print(f"날짜: {schedule['event_date']}")
            print(f"설명: {schedule['description'] or '-'}")
            print(f"등록일: {schedule['created_at']}")
            print(f"수정일: {schedule['updated_at']}")
            
            # D-Day 계산
            event_date = datetime.strptime(schedule['event_date'], '%Y-%m-%d')
            today_date = datetime.now()
            delta = (event_date.date() - today_date.date()).days
            
            if delta < 0:
                print(f"상태: 종료됨 (D+{abs(delta)})")
            elif delta == 0:
                print(f"상태: 🔥 오늘 진행!")
            else:
                print(f"상태: D-{delta} (앞으로 {delta}일 남음)")
            
            print("="*60 + "\n")
            return schedule
        except sqlite3.Error as e:
            print(f"❌ 일정 조회 실패: {e}")
            return None
    
    def update_schedule(self, schedule_id, title=None, location=None, event_date=None, description=None):
        """일정 수정"""
        try:
            # 기존 일정 확인
            self.cursor.execute('SELECT * FROM schedules WHERE id = ?', (schedule_id,))
            schedule = self.cursor.fetchone()
            
            if not schedule:
                print(f"❌ ID {schedule_id}번 일정을 찾을 수 없습니다.")
                return False
            
            # 수정할 필드만 업데이트
            updates = []
            params = []
            
            if title is not None:
                updates.append('title = ?')
                params.append(title)
            if location is not None:
                updates.append('location = ?')
                params.append(location)
            if event_date is not None:
                # 날짜 형식 검증
                try:
                    datetime.strptime(event_date, '%Y-%m-%d')
                    updates.append('event_date = ?')
                    params.append(event_date)
                except ValueError:
                    print("❌ 날짜 형식 오류. YYYY-MM-DD 형식으로 입력하세요.")
                    return False
            if description is not None:
                updates.append('description = ?')
                params.append(description)
            
            if not updates:
                print("⚠️  수정할 내용이 없습니다.")
                return False
            
            updates.append('updated_at = CURRENT_TIMESTAMP')
            params.append(schedule_id)
            
            query = f"UPDATE schedules SET {', '.join(updates)} WHERE id = ?"
            self.cursor.execute(query, params)
            self.conn.commit()
            
            print(f"✅ ID {schedule_id}번 일정이 수정되었습니다.")
            return True
        except sqlite3.Error as e:
            print(f"❌ 일정 수정 실패: {e}")
            return False
    
    def delete_schedule(self, schedule_id):
        """일정 삭제"""
        try:
            # 기존 일정 확인
            self.cursor.execute('SELECT * FROM schedules WHERE id = ?', (schedule_id,))
            schedule = self.cursor.fetchone()
            
            if not schedule:
                print(f"❌ ID {schedule_id}번 일정을 찾을 수 없습니다.")
                return False
            
            self.cursor.execute('DELETE FROM schedules WHERE id = ?', (schedule_id,))
            self.conn.commit()
            
            print(f"✅ ID {schedule_id}번 일정이 삭제되었습니다: {schedule['title']}")
            return True
        except sqlite3.Error as e:
            print(f"❌ 일정 삭제 실패: {e}")
            return False
    
    def search_schedules(self, keyword):
        """일정 검색 (제목, 장소, 설명)"""
        try:
            query = '''
                SELECT * FROM schedules 
                WHERE title LIKE ? OR location LIKE ? OR description LIKE ?
                ORDER BY event_date DESC
            '''
            search_term = f'%{keyword}%'
            self.cursor.execute(query, (search_term, search_term, search_term))
            schedules = self.cursor.fetchall()
            
            if not schedules:
                print(f"🔍 '{keyword}' 검색 결과가 없습니다.")
                return []
            
            print(f"\n🔍 '{keyword}' 검색 결과: {len(schedules)}건")
            
            # 테이블 형식으로 출력
            table_data = []
            for schedule in schedules:
                table_data.append([
                    schedule['id'],
                    schedule['title'],
                    schedule['location'] or '-',
                    schedule['event_date'],
                    schedule['description'][:30] + '...' if schedule['description'] and len(schedule['description']) > 30 else schedule['description'] or '-'
                ])
            
            headers = ['ID', '제목', '장소', '날짜', '설명']
            print(tabulate(table_data, headers=headers, tablefmt='grid'))
            print()
            
            return schedules
        except sqlite3.Error as e:
            print(f"❌ 검색 실패: {e}")
            return []
    
    def get_statistics(self):
        """일정 통계"""
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            
            # 전체 일정 수
            self.cursor.execute('SELECT COUNT(*) as total FROM schedules')
            total = self.cursor.fetchone()['total']
            
            # 다가오는 일정 수
            self.cursor.execute('SELECT COUNT(*) as upcoming FROM schedules WHERE event_date >= ?', (today,))
            upcoming = self.cursor.fetchone()['upcoming']
            
            # 지난 일정 수
            self.cursor.execute('SELECT COUNT(*) as past FROM schedules WHERE event_date < ?', (today,))
            past = self.cursor.fetchone()['past']
            
            # 이번 주 일정 수
            week_future = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')
            self.cursor.execute('SELECT COUNT(*) as week FROM schedules WHERE event_date >= ? AND event_date <= ?', (today, week_future))
            week = self.cursor.fetchone()['week']
            
            # 이번 달 일정 수
            month_future = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
            self.cursor.execute('SELECT COUNT(*) as month FROM schedules WHERE event_date >= ? AND event_date <= ?', (today, month_future))
            month = self.cursor.fetchone()['month']
            
            print("\n" + "="*60)
            print("📊 비행 스케줄 통계")
            print("="*60)
            print(f"전체 일정: {total}건")
            print(f"다가오는 일정: {upcoming}건")
            print(f"지난 일정: {past}건")
            print(f"이번 주 일정: {week}건")
            print(f"이번 달 일정: {month}건")
            print("="*60 + "\n")
            
        except sqlite3.Error as e:
            print(f"❌ 통계 조회 실패: {e}")


def print_menu():
    """메뉴 출력"""
    print("\n" + "="*60)
    print("✈️  Virtual Black Eagles - 비행 스케줄 관리")
    print("="*60)
    print("1. 📋 일정 목록 조회")
    print("2. ➕ 일정 추가")
    print("3. 🔍 일정 검색")
    print("4. 📝 일정 상세 조회")
    print("5. ✏️  일정 수정")
    print("6. 🗑️  일정 삭제")
    print("7. 📊 통계")
    print("0. 🚪 종료")
    print("="*60)


def main():
    """메인 함수"""
    manager = FlightScheduleManager()
    
    if not manager.connect():
        print("데이터베이스 연결에 실패했습니다.")
        return
    
    try:
        while True:
            print_menu()
            choice = input("선택: ").strip()
            
            if choice == '1':
                # 일정 목록 조회
                print("\n📋 일정 필터:")
                print("1. 전체")
                print("2. 다가오는 일정")
                print("3. 지난 일정")
                print("4. 오늘")
                print("5. 이번 주")
                print("6. 이번 달")
                
                filter_choice = input("선택 (기본: 1): ").strip() or '1'
                
                filter_map = {
                    '1': 'all',
                    '2': 'upcoming',
                    '3': 'past',
                    '4': 'today',
                    '5': 'week',
                    '6': 'month'
                }
                
                manager.list_schedules(filter_map.get(filter_choice, 'all'))
            
            elif choice == '2':
                # 일정 추가
                print("\n➕ 새 일정 추가")
                title = input("제목: ").strip()
                location = input("장소: ").strip()
                event_date = input("날짜 (YYYY-MM-DD): ").strip()
                description = input("설명 (선택): ").strip()
                
                if title and event_date:
                    manager.add_schedule(title, location, event_date, description)
                else:
                    print("❌ 제목과 날짜는 필수입니다.")
            
            elif choice == '3':
                # 일정 검색
                keyword = input("\n🔍 검색어: ").strip()
                if keyword:
                    manager.search_schedules(keyword)
                else:
                    print("❌ 검색어를 입력하세요.")
            
            elif choice == '4':
                # 일정 상세 조회
                schedule_id = input("\n📝 조회할 일정 ID: ").strip()
                if schedule_id.isdigit():
                    manager.get_schedule(int(schedule_id))
                else:
                    print("❌ 올바른 ID를 입력하세요.")
            
            elif choice == '5':
                # 일정 수정
                schedule_id = input("\n✏️  수정할 일정 ID: ").strip()
                if not schedule_id.isdigit():
                    print("❌ 올바른 ID를 입력하세요.")
                    continue
                
                print("수정할 항목을 입력하세요 (Enter: 건너뛰기)")
                title = input("제목: ").strip() or None
                location = input("장소: ").strip() or None
                event_date = input("날짜 (YYYY-MM-DD): ").strip() or None
                description = input("설명: ").strip() or None
                
                manager.update_schedule(int(schedule_id), title, location, event_date, description)
            
            elif choice == '6':
                # 일정 삭제
                schedule_id = input("\n🗑️  삭제할 일정 ID: ").strip()
                if not schedule_id.isdigit():
                    print("❌ 올바른 ID를 입력하세요.")
                    continue
                
                confirm = input(f"정말 ID {schedule_id}번 일정을 삭제하시겠습니까? (y/n): ").strip().lower()
                if confirm == 'y':
                    manager.delete_schedule(int(schedule_id))
            
            elif choice == '7':
                # 통계
                manager.get_statistics()
            
            elif choice == '0':
                print("\n👋 프로그램을 종료합니다.")
                break
            
            else:
                print("❌ 올바른 메뉴를 선택하세요.")
            
            input("\nEnter 키를 눌러 계속...")
    
    finally:
        manager.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n프로그램이 중단되었습니다.")
        sys.exit(0)
