import os
import subprocess
import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
from datetime import datetime, timedelta
import zipfile
import shutil
import logging
import sys
import time
import threading
import tempfile
import traceback
import json
from functools import wraps
import hashlib

# ==========================
# إعداد السجلات المتقدمة
# ==========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# ==========================
# إعدادات البوت والمشروع
# ==========================
# التوكن - استبدله بتوكن البوت الخاص بك من BotFather
BOT_TOKEN = "8928231894:AAF1rlhz1UuvLEt5NGwuOzPyP6LW5ppyv4A"

# قائمة المالكين
ADMIN_IDS = [6533075996]  # استبدل بأرقام معرفات المالكين

# إعدادات المشروع
MAX_RETRIES = 3
PROJECTS_DIR = "projects"
REQUIREMENTS_FILE = "requirements.txt"
AUTO_RESTART_DELAY = 60  # ثواني قبل إعادة التشغيل التلقائي
ALLOWED_USERS_FILE = "allowed_users.json"  # ملف تخزين المستخدمين المسموح لهم
MAX_SCRIPTS = 2  # أقصى عدد من السكربتات التي يمكن تشغيلها معاً
GLOBAL_RESTART_INTERVAL = 18300  # 10 دقائق بالثواني
USER_PROJECTS_FILE = "user_projects.json"  # ملف لتخزين مشاريع المستخدمين

# ==========================
# التحقق من التوكن قبل تهيئة البوت
# ==========================
if not BOT_TOKEN or BOT_TOKEN.strip() == "":
    raise ValueError("❌ التوكن غير صالح أو فارغ! ضع توكن البوت الخاص بك من BotFather.")

# ==========================
# تهيئة البوت
# ==========================
bot = telebot.TeleBot(BOT_TOKEN)
logger.info("✅ تم تهيئة البوت بنجاح!")

# ==========================
# دالة لمعالجة الأخطاء وإعادة المحاولة تلقائياً
# ==========================
def error_handler(func):
    """مصمم لمعالجة الأخطاء وإعادة المحاولة تلقائياً"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        retries = 0
        while retries < MAX_RETRIES:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(f"Error in {func.__name__}: {str(e)}\n{traceback.format_exc()}")
                retries += 1
                if retries < MAX_RETRIES:
                    logger.info(f"⏳ إعادة المحاولة {retries}/{MAX_RETRIES} بعد 5 ثواني...")
                    time.sleep(5)
                    continue
                logger.critical(f"💥 فشل العملية بعد {MAX_RETRIES} محاولات")
                raise
    return wrapper

# ==========================
# مثال لاستخدام error_handler مع دالة إرسال رسالة
# ==========================
@error_handler
def send_message_safe(chat_id, text, reply_markup=None):
    bot.send_message(chat_id, text, reply_markup=reply_markup)

logger.info("📌 البوت جاهز للتشغيل، يمكنك إضافة الأوامر والمعالجات الآن")

class UserManager:
    """إدارة المستخدمين المسموح لهم باستخدام البوت"""
    def __init__(self):
        self.allowed_users = set(ADMIN_IDS)  # المالكون الأساسيون
        self.load_allowed_users()
    
    def load_allowed_users(self):
        """تحميل قائمة المستخدمين من الملف"""
        try:
            if os.path.exists(ALLOWED_USERS_FILE):
                with open(ALLOWED_USERS_FILE, 'r') as f:
                    data = json.load(f)
                    self.allowed_users.update(data.get('allowed_users', []))
        except Exception as e:
            logger.error(f"خطأ في تحميل المستخدمين: {str(e)}")
    
    def save_allowed_users(self):
        """حفظ قائمة المستخدمين إلى الملف"""
        try:
            data = {'allowed_users': list(self.allowed_users)}
            with open(ALLOWED_USERS_FILE, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            logger.error(f"خطأ في حفظ المستخدمين: {str(e)}")
    
    def add_user(self, user_id: int):
        """إضافة مستخدم جديد"""
        self.allowed_users.add(user_id)
        self.save_allowed_users()
    
    def remove_user(self, user_id: int):
        """إزالة مستخدم"""
        if user_id in self.allowed_users and user_id not in ADMIN_IDS:
            self.allowed_users.remove(user_id)
            self.save_allowed_users()
            return True
        return False
    
    def is_allowed(self, user_id: int) -> bool:
        """التحقق من صلاحية المستخدم"""
        return user_id in self.allowed_users
    
    def list_users(self):
        """الحصول على قائمة جميع المستخدمين"""
        return list(self.allowed_users)

class ProjectManager:
    """إدارة المشاريع وعمليات التشغيل والإيقاف"""
    def __init__(self, user_manager):
        self.running_processes = {}
        self.user_projects = {}
        self.paused_processes = {}
        self.waiting_for_main_file = {}
        self.waiting_for_duration = {}
        self.keep_running = True
        self.global_restart_thread = None
        self.user_manager = user_manager
        self.script_hashes = {}  # لتخزين الهاش والبيانات
        
        # إنشاء مجلد المشاريع إذا لم يكن موجوداً
        os.makedirs(PROJECTS_DIR, exist_ok=True)
        
        # تحميل مشاريع المستخدمين المخزنة
        self.load_user_projects()
        
        # بدء نظام إعادة التشغيل الشامل
        self.start_global_restart()
    
    def load_user_projects(self):
        """تحميل مشاريع المستخدمين من ملف"""
        try:
            if os.path.exists(USER_PROJECTS_FILE):
                with open(USER_PROJECTS_FILE, 'r') as f:
                    data = json.load(f)
                    # تحويل المفاتيح من نص إلى أعداد صحيحة
                    self.user_projects = {int(k): v for k, v in data.items()}
                    logger.info(f"تم تحميل {len(self.user_projects)} مشاريع للمستخدمين")
        except Exception as e:
            logger.error(f"خطأ في تحميل مشاريع المستخدمين: {str(e)}")
    
    def save_user_projects(self):
        """حفظ مشاريع المستخدمين إلى ملف"""
        try:
            with open(USER_PROJECTS_FILE, 'w') as f:
                json.dump(self.user_projects, f)
            logger.info("تم حفظ مشاريع المستخدمين بنجاح")
        except Exception as e:
            logger.error(f"خطأ في حفظ مشاريع المستخدمين: {str(e)}")
    
    def get_python_scripts(self, project_dir: str) -> list:
        """الحصول على قائمة بجميع ملفات البايثون في المشروع"""
        python_files = []
        for root, dirs, files in os.walk(project_dir):
            for file in files:
                if file.endswith('.py'):
                    rel_path = os.path.relpath(os.path.join(root, file), project_dir)
                    python_files.append(rel_path.replace('\\', '/'))  # توحيد المسارات لنظام Unix/Windows
        return python_files
    
    def start_global_restart(self):
        """بدء نظام إعادة تشغيل جميع المشاريع كل 10 دقائق"""
        if self.global_restart_thread and self.global_restart_thread.is_alive():
            return
            
        self.global_restart_thread = threading.Thread(target=self._global_restart_projects)
        self.global_restart_thread.daemon = True
        self.global_restart_thread.start()
    
    def _global_restart_projects(self):
        """إعادة تشغيل جميع المشاريع كل فترة محددة"""
        while self.keep_running:
            try:
                time.sleep(GLOBAL_RESTART_INTERVAL)
                
                if not self.running_processes:
                    continue
                
                logger.info("بدء إعادة تشغيل جميع المشاريع...")
                
                for project_dir, process_info in list(self.running_processes.items()):
                    chat_id = process_info['chat_id']
                    project_name = process_info['project_name']
                    main_files = process_info['main_files']
                    end_time = process_info.get('end_time')
                    auto_restart = process_info.get('auto_restart', True)
                    
                    if not auto_restart:
                        continue
                    
                    try:
                        # إيقاف المشروع الحالي
                        self.stop_project(project_dir, chat_id)
                        
                        # تشغيل المشروع مجدداً
                        duration_days = (end_time - datetime.now()).days if end_time else None
                        self.run_project(project_dir, chat_id, duration_days, auto_restart)
                        
                        logger.info(f"تم إعادة تشغيل المشروع: {project_name}")
                        bot.send_message(chat_id, f"🔄 تم إعادة تشغيل المشروع: {project_name} تلقائياً")
                    except Exception as e:
                        logger.error(f"فشل إعادة تشغيل المشروع {project_name}: {str(e)}")
                        bot.send_message(chat_id, f"❌ فشل إعادة تشغيل المشروع: {project_name}")
                
                logger.info("تم إعادة تشغيل جميع المشاريع بنجاح")
            except Exception as e:
                logger.error(f"خطأ في إعادة التشغيل الشامل: {str(e)}\n{traceback.format_exc()}")
                time.sleep(30)

    @error_handler
    def install_requirements(self, project_dir: str, chat_id: int) -> bool:
        """تثبيت المكتبات المطلوبة من ملف requirements.txt"""
        requirements_path = os.path.join(project_dir, REQUIREMENTS_FILE)
        if os.path.exists(requirements_path):
            try:
                logger.info(f"جاري تثبيت المكتبات للمشروع في {project_dir}")
                process = subprocess.run(
                    ['pip', 'install', '-r', requirements_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=300  # 5 دقائق كحد أقصى
                )
                
                if process.returncode != 0:
                    error_msg = f"❌ فشل تثبيت المكتبات:\n{process.stderr[:1000]}"
                    bot.send_message(chat_id, error_msg)
                    logger.error(f"فشل تثبيت المكتبات: {process.stderr}")
                    return False
                
                logger.info("تم تثبيت المكتبات بنجاح")
                return True
            except subprocess.TimeoutExpired:
                error_msg = "❌ تجاوز وقت تثبيت المكتبات الحد الأقصى (5 دقائق)"
                bot.send_message(chat_id, error_msg)
                logger.error("تجاوز وقت تثبيت المكتبات الحد الأقصى")
                return False
            except Exception as e:
                error_msg = f"❌ خطأ في تثبيت المكتبات: {str(e)}"
                bot.send_message(chat_id, error_msg)
                logger.error(f"خطأ في تثبيت المكتبات: {str(e)}")
                return False
        return True

    @error_handler
    def run_project(self, project_dir: str, chat_id: int, duration_days: int = None, auto_restart: bool = True):
        """تشغيل المشروع مع تحديد المدة"""
        main_files = []
        user_id = self.get_user_id_by_chat_id(chat_id)
        
        # البحث عن الملفات الرئيسية في user_projects
        if user_id in self.user_projects:
            for project_info in self.user_projects[user_id]:
                if project_info['project_dir'] == project_dir:
                    main_files = project_info['main_files']
                    break

        # إذا لم يتم العثور على الملفات الرئيسية، تحقق من waiting_for_main_file
        if not main_files and user_id in self.waiting_for_main_file:
            if 'scripts_to_run' in self.waiting_for_main_file[user_id]:
                main_files = self.waiting_for_main_file[user_id]['scripts_to_run']
            
            # حفظ المشروع في user_projects إذا كان في waiting_for_main_file
            if main_files and user_id not in self.user_projects:
                self.user_projects[user_id] = []
                
            if main_files and user_id in self.user_projects:
                self.user_projects[user_id].append({
                    'project_dir': project_dir,
                    'project_name': os.path.basename(project_dir),
                    'upload_time': datetime.now().isoformat(),
                    'chat_id': chat_id,
                    'pinned': False,
                    'main_files': main_files,
                    'num_scripts': len(main_files)
                })
                self.save_user_projects()

        if not main_files:
            bot.send_message(chat_id, "⚠️ لم يتم العثور على الملف/الملفات الرئيسية المحددة")
            logger.error(f"الملفات الرئيسية غير موجودة في قاعدة البيانات للمشروع: {project_dir}")
            return False

        # التحقق من وجود الملفات فعلياً
        missing_files = [f for f in main_files if not os.path.exists(f)]
        if missing_files:
            bot.send_message(chat_id, f"⚠️ الملفات التالية غير موجودة: {', '.join(missing_files)}")
            logger.error(f"الملفات الرئيسية غير موجودة: {missing_files}")
            return False

        if not self.install_requirements(project_dir, chat_id):
            return False

        try:
            logger.info(f"جاري تشغيل المشروع: {main_files}")
            
            # تشغيل جميع السكربتات المحددة
            processes = []
            for main_file in main_files:
                # استخدام المسار المطلق الصحيح
                abs_main_file = os.path.abspath(main_file)
                if not os.path.isfile(abs_main_file):
                    bot.send_message(chat_id, f"⚠️ الملف غير موجود: {abs_main_file}")
                    logger.error(f"الملف غير موجود: {abs_main_file}")
                    continue
                    
                process = subprocess.Popen(
                    ['python', abs_main_file],
                    cwd=os.path.dirname(abs_main_file) or project_dir,  # استخدام مجلد المشروع
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )
                processes.append(process)
            
            if not processes:
                bot.send_message(chat_id, "❌ لم يتم تشغيل أي ملفات، تأكد من وجود الملفات")
                return False
            
            end_time = datetime.now() + timedelta(days=duration_days) if duration_days else None
            
            self.running_processes[project_dir] = {
                'processes': processes,  # قائمة بجميع العمليات
                'chat_id': chat_id,
                'start_time': datetime.now(),
                'end_time': end_time,
                'project_name': os.path.basename(project_dir),
                'user_id': user_id,
                'pinned': False,
                'main_files': main_files,
                'auto_restart': auto_restart
            }
            
            if project_dir in self.paused_processes:
                del self.paused_processes[project_dir]
            
            # بدء قراءة المخرجات لكل عملية
            for process in processes:
                self.start_output_reader(process, chat_id, os.path.basename(project_dir))
            
            logger.info(f"تم تشغيل المشروع بنجاح: {project_dir}")
            bot.send_message(chat_id, f"✅ بدأ تشغيل المشروع: {os.path.basename(project_dir)}")
            return True
            
        except Exception as e:
            error_msg = f"⚠️ فشل بدء التشغيل: {str(e)}"
            bot.send_message(chat_id, error_msg)
            logger.error(f"فشل تشغيل المشروع: {str(e)}")
            return False

    @error_handler
    def start_output_reader(self, process, chat_id, project_name):
        """قراءة مخرجات المشروع وإرسال الأخطاء إلى المستخدم"""
        def reader():
            try:
                while True:
                    output = process.stdout.readline()
                    if output == '' and process.poll() is not None:
                        break
                    if output:
                        logger.info(f"Output from {project_name}: {output.strip()}")
                
                error = process.stderr.read()
                if error:
                    logger.error(f"Error from {project_name}: {error}")
                    bot.send_message(chat_id, f"❌ خطأ في المشروع {project_name}:\n{error[:3000]}")
                    
                if process.returncode != 0:
                    bot.send_message(chat_id, f"⚠️ توقف المشروع {project_name} مع كود خروج: {process.returncode}")
            except Exception as e:
                logger.error(f"Error in output reader: {str(e)}")

        thread = threading.Thread(target=reader)
        thread.daemon = True
        thread.start()

    @error_handler
    def stop_project(self, project_dir: str, chat_id: int, pause=False):
        """إيقاف مشروع يعمل"""
        if project_dir in self.running_processes:
            process_info = self.running_processes[project_dir]
            
            try:
                logger.info(f"جاري إيقاف المشروع: {project_dir}")
                
                # إيقاف جميع العمليات
                for process in process_info['processes']:
                    try:
                        process.terminate()
                        process.wait(timeout=5)
                    except Exception as e:
                        logger.error(f"Error stopping process: {str(e)}")
                        try:
                            process.kill()
                        except:
                            pass
                
                if pause:
                    self.paused_processes[project_dir] = process_info
                
                del self.running_processes[project_dir]
                
                if not pause:
                    bot.send_message(chat_id, f"⏹️ تم إيقاف المشروع: {os.path.basename(project_dir)} بنجاح")
                    logger.info(f"تم إيقاف المشروع: {project_dir}")
                return True
            except Exception as e:
                error_msg = f"❌ فشل إيقاف المشروع: {str(e)}"
                bot.send_message(chat_id, error_msg)
                logger.error(f"فشل إيقاف المشروع: {str(e)}")
                return False
        else:
            bot.send_message(chat_id, "⚠️ لا يوجد مشروع يعمل بهذا الاسم حالياً")
            return False

    def get_user_id_by_chat_id(self, chat_id: int) -> int:
        """الحصول على معرف المستخدم من معرف المحادثة"""
        for user_id, projects in self.user_projects.items():
            for project in projects:
                if project['chat_id'] == chat_id:
                    return user_id
        return None

    def cleanup(self):
        """تنظيف الموارد عند إيقاف البوت"""
        self.keep_running = False
        for project_dir in list(self.running_processes.keys()):
            self.stop_project(project_dir, self.running_processes[project_dir]['chat_id'])
        logger.info("تم تنظيف جميع الموارد وإيقاف العمليات")

class PythonHostingBot:
    """الفئة الرئيسية للبوت مع واجهة المستخدم"""
    def __init__(self):
        self.user_manager = UserManager()
        self.manager = ProjectManager(self.user_manager)
        self.setup_handlers()

    def check_access(self, user_id: int) -> bool:
        return self.user_manager.is_allowed(user_id)

    def setup_handlers(self):
        @bot.message_handler(commands=['start', 'help'])
        @error_handler
        def start(message):
            if not self.check_access(message.from_user.id):
                bot.reply_to(message, "⛔ عذراً، ليس لديك صلاحية الوصول إلى هذا البوت.")
                return
                
            welcome_msg = """
            👑 <b>مرحباً في نظام استضافة مشاريع masry   host المتكامل</b> 👑
            
            <b>ميزات النظام:</b>
            - نظام ملكية متكامل👩‍💻
            - دعم المشاريع متعددة الملفات📁
            - تحديد الملف الرئيسي يدوياً👆
            - تثبيت المكتبات من requirements.txt✍⌨️
            - تحديد مدة التشغيل📶
            - واجهة تفاعلية محسنة⭐
            - إعادة تشغيل دورية كل 10 دقائق🚀
            
            <b>الأوامر المتاحة:</b>
            /start - عرض هذه الرسالة📃
            /myprojects - عرض مشاريعك🏴‍☠️
            /stopall - إيقاف جميع مشاريعك❌
            /pause - إيقاف مؤقت للمشاريع⛔
            /on - استئناف المشاريع المتوقفة🔁
            /clear - حذف جميع المشاريع🚫
            
            <b>أوامر المالكين:</b>
            /adduser [id] - إضافة مستخدم
            /removeuser [id] - إزالة مستخدم
            /listusers - عرض جميع المستخدمين
            
            أرسل ملفات مشروعك كمضغوط (zip) للبدء
            """
            bot.reply_to(message, welcome_msg, parse_mode='HTML')
            logger.info(f"تم عرض رسالة الترحيب للمستخدم: {message.from_user.id}")

        @bot.message_handler(commands=['adduser'])
        @error_handler
        def add_user_command(message):
            """إضافة مستخدم جديد"""
            if message.from_user.id not in ADMIN_IDS:
                bot.reply_to(message, "⛔ هذا الأمر متاح فقط للمالكين")
                return
            
            try:
                user_id = int(message.text.split()[1])
                self.user_manager.add_user(user_id)
                bot.reply_to(message, f"✅ تم إضافة المستخدم: {user_id}")
                logger.info(f"تم إضافة مستخدم جديد: {user_id}")
            except (IndexError, ValueError):
                bot.reply_to(message, "❌ استخدم: /adduser <user_id>")
            except Exception as e:
                bot.reply_to(message, f"❌ خطأ: {str(e)}")

        @bot.message_handler(commands=['removeuser'])
        @error_handler
        def remove_user_command(message):
            """إزالة مستخدم"""
            if message.from_user.id not in ADMIN_IDS:
                bot.reply_to(message, "⛔ هذا الأمر متاح فقط للمالكين")
                return
            
            try:
                user_id = int(message.text.split()[1])
                if self.user_manager.remove_user(user_id):
                    bot.reply_to(message, f"✅ تم إزالة المستخدم: {user_id}")
                    logger.info(f"تم إزالة مستخدم: {user_id}")
                else:
                    bot.reply_to(message, "❌ لا يمكن إزالة المالكين الأساسيين")
            except (IndexError, ValueError):
                bot.reply_to(message, "❌ استخدم: /removeuser <user_id>")
            except Exception as e:
                bot.reply_to(message, f"❌ خطأ: {str(e)}")

        @bot.message_handler(commands=['listusers'])
        @error_handler
        def list_users_command(message):
            """عرض قائمة المستخدمين"""
            if message.from_user.id not in ADMIN_IDS:
                bot.reply_to(message, "⛔ هذا الأمر متاح فقط للمالكين")
                return
            
            users = self.user_manager.list_users()
            response = "👥 <b>قائمة المستخدمين المسموح لهم:</b>\n\n"
            for user_id in users:
                status = "👑 مالك" if user_id in ADMIN_IDS else "👤 مستخدم"
                response += f"- {user_id} ({status})\n"
            
            bot.reply_to(message, response, parse_mode='HTML')

        @bot.message_handler(commands=['myprojects'])
        @error_handler
        def show_user_projects(message):
            if not self.check_access(message.from_user.id):
                bot.reply_to(message, "⛔ عذراً، ليس لديك صلاحية الوصول إلى هذا البوت.")
                return
                
            user_id = message.from_user.id
            if user_id not in self.manager.user_projects or not self.manager.user_projects[user_id]:
                bot.reply_to(message, "📂 لا يوجد لديك أي مشاريع مخزنة حالياً.")
                return
                
            response = "📂 <b>مشاريعك المخزنة:</b>\n\n"
            
            for idx, project_info in enumerate(self.manager.user_projects[user_id], 1):
                project_dir = project_info['project_dir']
                project_name = os.path.basename(project_dir)
                is_running = project_dir in self.manager.running_processes
                is_paused = project_dir in self.manager.paused_processes
                
                if is_running:
                    status = "🟢 قيد التشغيل"
                    if self.manager.running_processes[project_dir]['end_time']:
                        remaining = self.manager.running_processes[project_dir]['end_time'] - datetime.now()
                        status += f" (متبقي: {remaining.days} يوم)"
                elif is_paused:
                    status = "🟡 متوقف مؤقتاً"
                else:
                    status = "🔴 متوقف"
                
                response += f"{idx}. <b>{project_name}</b> - {status}\n"
                
                # إضافة أزرار التحكم لكل مشروع
                keyboard = []
                if is_running:
                    keyboard.append([
                        InlineKeyboardButton("⏹️ إيقاف", callback_data=f'stop_{project_dir}'),
                        InlineKeyboardButton("⏸️ إيقاف مؤقت", callback_data=f'pause_{project_dir}')
                    ])
                    keyboard.append([
                        InlineKeyboardButton("⏳ تحديد المدة", callback_data=f'duration_{project_dir}'),
                        InlineKeyboardButton("🔄 إعادة تشغيل", callback_data=f'restart_{project_dir}')
                    ])
                elif is_paused:
                    keyboard.append([
                        InlineKeyboardButton("▶️ استئناف", callback_data=f'resume_{project_dir}'),
                        InlineKeyboardButton("⏹️ إيقاف", callback_data=f'stop_{project_dir}')
                    ])
                else:
                    keyboard.append([
                        InlineKeyboardButton("▶️ تشغيل", callback_data=f'run_{project_dir}'),
                        InlineKeyboardButton("🗑️ حذف", callback_data=f'delete_{project_dir}')
                    ])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                bot.send_message(message.chat.id, response, parse_mode='HTML', reply_markup=reply_markup)
                response = ""  # مسح الرد بعد إرسال كل مشروع
            
            if not response:  # إذا تم إرسال جميع المشاريع
                return
                
            # إذا لم يتم إرسال أي مشروع (جميعها مثبتة)
            bot.send_message(message.chat.id, response, parse_mode='HTML')

        @bot.message_handler(content_types=['document'])
        @error_handler
        def handle_document(message):
            if not self.check_access(message.from_user.id):
                bot.reply_to(message, "⛔ عذراً، ليس لديك صلاحية الوصول إلى هذا البوت.")
                return
                
            file_name = message.document.file_name
            if not file_name.endswith('.zip'):
                bot.reply_to(message, "⚠️ يرجى إرسال ملف مضغوط بصيغة zip فقط")
                return
                
            try:
                # إنشاء مجلد مؤقت لاستقبال الملف
                temp_dir = tempfile.mkdtemp()
                temp_zip_path = os.path.join(temp_dir, file_name)
                
                # تنزيل الملف
                file_info = bot.get_file(message.document.file_id)
                downloaded_file = bot.download_file(file_info.file_path)
                
                with open(temp_zip_path, 'wb') as new_file:
                    new_file.write(downloaded_file)
                
                # استخراج الملف المضغوط
                project_name = os.path.splitext(file_name)[0]
                user_dir = os.path.join(PROJECTS_DIR, str(message.from_user.id))
                os.makedirs(user_dir, exist_ok=True)
                project_dir = os.path.join(user_dir, project_name)
                
                # حذف المجلد إذا كان موجوداً مسبقاً
                if os.path.exists(project_dir):
                    shutil.rmtree(project_dir)
                
                with zipfile.ZipFile(temp_zip_path, 'r') as zip_ref:
                    zip_ref.extractall(project_dir)
                
                # تنظيف الملف المؤقت
                os.remove(temp_zip_path)
                os.rmdir(temp_dir)
                
                # حفظ معلومات المشروع
                self.manager.waiting_for_main_file[message.from_user.id] = {
                    'project_dir': project_dir,
                    'project_name': project_name,
                    'chat_id': message.chat.id,
                    'scripts_to_run': []  # قائمة لتخزين السكربتات المطلوبة
                }
                
                # إنشاء لوحة أزرار لاختيار عدد السكربتات
                keyboard = [
                    [InlineKeyboardButton("سكربت واحد", callback_data=f'scriptnum_1_{project_dir}')],
                    [InlineKeyboardButton("سكربتين معاً", callback_data=f'scriptnum_2_{project_dir}')]
                ]
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                bot.send_message(
                    message.chat.id,
                    f"📦 تم استلام مشروع: {project_name}\n\n"
                    "🔢 يرجى اختيار عدد السكربتات التي تريد تشغيلها:",
                    reply_markup=reply_markup
                )
                logger.info(f"تم استلام مشروع جديد من المستخدم: {message.from_user.id}")
                
            except Exception as e:
                error_msg = f"❌ فشل معالجة الملف: {str(e)}"
                bot.reply_to(message, error_msg)
                logger.error(f"فشل معالجة الملف: {str(e)}")
                if 'temp_dir' in locals() and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)

        @bot.callback_query_handler(func=lambda call: call.data.startswith('scriptnum_'))
        @error_handler
        def handle_script_number(call):
            user_id = call.from_user.id
            if user_id not in self.manager.waiting_for_main_file:
                bot.answer_callback_query(call.id, "❌ انتهت صلاحية الجلسة، يرجى إعادة إرسال المشروع")
                return
            
            parts = call.data.split('_', 2)
            num_scripts = int(parts[1])
            project_dir = parts[2]
            
            # تحديث عدد السكربتات المطلوبة
            self.manager.waiting_for_main_file[user_id]['num_scripts'] = num_scripts
            
            # الحصول على جميع ملفات البايثون في المشروع
            python_scripts = self.manager.get_python_scripts(project_dir)
            
            if not python_scripts:
                bot.edit_message_text(
                    "⚠️ لم يتم العثور على أي ملفات بايثون (.py) في المشروع",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id
                )
                return
            
            if num_scripts == 1:
                # إنشاء أزرار لاختيار سكربت واحد
                keyboard = []
                for script in python_scripts:
                    # استخدام التجزئة لتقليل طول البيانات
                    data_str = f"{project_dir}|{script}"
                    data_hash = hashlib.md5(data_str.encode()).hexdigest()
                    self.manager.script_hashes[data_hash] = (project_dir, script)
                    keyboard.append([InlineKeyboardButton(script, callback_data=f'scriptselect_{data_hash}')])
                
                keyboard.append([InlineKeyboardButton("إلغاء", callback_data='cancel_selection')])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                bot.edit_message_text(
                    "📂 يرجى اختيار الملف الرئيسي من القائمة:",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=reply_markup
                )
            else:
                # إنشاء أزرار لاختيار سكربتين
                self.manager.waiting_for_main_file[user_id]['available_scripts'] = python_scripts
                bot.edit_message_text(
                    "📂 يرجى إرسال أسماء الملفين التي تريد تشغيلها معاً (مثال: main.py worker.py)\n"
                    "الملفات المتاحة:\n" + "\n".join(python_scripts) + "\n\n"
                    "أو اضغط /cancel للإلغاء",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id
                )

        @bot.callback_query_handler(func=lambda call: call.data.startswith('scriptselect_'))
        @error_handler
        def handle_script_selection(call):
            user_id = call.from_user.id
            if user_id not in self.manager.waiting_for_main_file:
                bot.answer_callback_query(call.id, "❌ انتهت صلاحية الجلسة، يرجى إعادة إرسال المشروع")
                return
            
            data_hash = call.data.split('_', 1)[1]
            
            if data_hash not in self.manager.script_hashes:
                bot.answer_callback_query(call.id, "❌ خطأ في بيانات الاستدعاء")
                return
                
            project_dir, script_name = self.manager.script_hashes[data_hash]
            del self.manager.script_hashes[data_hash]  # تنظيف البيانات
            
            # حفظ الملف المختار
            script_path = os.path.join(project_dir, script_name)
            self.manager.waiting_for_main_file[user_id]['scripts_to_run'] = [script_path]
            
            # طلب تحديد مدة التشغيل
            keyboard = [
                [InlineKeyboardButton("1 يوم", callback_data=f'duration_1_{project_dir}')],
                [InlineKeyboardButton("3 أيام", callback_data=f'duration_3_{project_dir}')],
                [InlineKeyboardButton("7 أيام", callback_data=f'duration_7_{project_dir}')],
                [InlineKeyboardButton("بدون مدة", callback_data=f'duration_0_{project_dir}')]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            bot.edit_message_text(
                f"📄 تم اختيار الملف الرئيسي: {script_name}\n\n"
                "⏳ يرجى تحديد مدة التشغيل للمشروع:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=reply_markup
            )

        @bot.message_handler(func=lambda message: message.from_user.id in self.manager.waiting_for_main_file and 
                      'num_scripts' in self.manager.waiting_for_main_file[message.from_user.id] and
                      self.manager.waiting_for_main_file[message.from_user.id]['num_scripts'] == 2)
        @error_handler
        def handle_two_scripts_names(message):
            if message.text == '/cancel':
                user_data = self.manager.waiting_for_main_file.pop(message.from_user.id)
                project_dir = user_data['project_dir']
                try:
                    shutil.rmtree(project_dir)
                    bot.send_message(message.chat.id, "❌ تم إلغاء العملية وحذف المشروع.")
                    logger.info(f"تم إلغاء تحميل المشروع: {project_dir}")
                except Exception as e:
                    bot.send_message(message.chat.id, f"❌ فشل حذف المشروع: {str(e)}")
                    logger.error(f"فشل حذف المشروع: {str(e)}")
                return
                
            user_data = self.manager.waiting_for_main_file[message.from_user.id]
            project_dir = user_data['project_dir']
            project_name = user_data['project_name']
            chat_id = user_data['chat_id']
            available_scripts = user_data.get('available_scripts', [])
            
            script_names = message.text.strip().split()
            
            # التحقق من عدد السكربتات
            if len(script_names) != 2:
                bot.send_message(
                    message.chat.id,
                    "⚠️ يرجى إرسال اسمي ملفين فقط.\n"
                    "أو اضغط /cancel للإلغاء"
                )
                return
            
            # التحقق من وجود الملفات
            missing_files = []
            main_files = []
            
            for script_name in script_names:
                script_path = os.path.join(project_dir, script_name)
                if not os.path.exists(script_path):
                    missing_files.append(script_name)
                else:
                    main_files.append(script_path)
            
            if missing_files:
                bot.send_message(
                    message.chat.id,
                    f"⚠️ الملفات التالية غير موجودة: {', '.join(missing_files)}\n"
                    "يرجى إرسال أسماء الملفات الصحيحة أو /cancel للإلغاء"
                )
                return
            
            # حفظ الملفات المختارة
            self.manager.waiting_for_main_file[message.from_user.id]['scripts_to_run'] = main_files
            
            # طلب تحديد مدة التشغيل
            keyboard = [
                [InlineKeyboardButton("1 يوم", callback_data=f'duration_1_{project_dir}')],
                [InlineKeyboardButton("3 أيام", callback_data=f'duration_3_{project_dir}')],
                [InlineKeyboardButton("7 أيام", callback_data=f'duration_7_{project_dir}')],
                [InlineKeyboardButton("بدون مدة", callback_data=f'duration_0_{project_dir}')]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            bot.send_message(
                message.chat.id,
                f"📦 مشروع: {project_name}\n"
                f"📄 الملفات الرئيسية: {', '.join(script_names)}\n\n"
                "⏳ يرجى تحديد مدة التشغيل للمشروع:",
                reply_markup=reply_markup
            )

        @bot.callback_query_handler(func=lambda call: call.data.startswith('duration_') and not call.data.startswith('duration_set_'))
        @error_handler
        def handle_initial_duration(call):
            parts = call.data.split('_')
            days = int(parts[1])
            project_dir = '_'.join(parts[2:])
            chat_id = call.message.chat.id
            user_id = call.from_user.id
            
            # تحقق من وجود المشروع في waiting_for_main_file
            if user_id not in self.manager.waiting_for_main_file:
                bot.answer_callback_query(call.id, "❌ انتهت صلاحية الجلسة، يرجى إعادة إرسال المشروع")
                return
            
            user_data = self.manager.waiting_for_main_file[user_id]
            main_files = user_data.get('scripts_to_run', [])
            
            if not main_files:
                bot.answer_callback_query(call.id, "❌ لم يتم تحديد ملف رئيسي")
                return
            
            install_success = self.manager.install_requirements(project_dir, chat_id)
            
            # تخزين معلومات المشروع
            if user_id not in self.manager.user_projects:
                self.manager.user_projects[user_id] = []
                
            self.manager.user_projects[user_id].append({
                'project_dir': project_dir,
                'project_name': os.path.basename(project_dir),
                'upload_time': datetime.now().isoformat(),
                'chat_id': chat_id,
                'pinned': False,
                'main_files': main_files,
                'num_scripts': len(main_files)
            })
            self.manager.save_user_projects()
            
            # تنظيف waiting_for_main_file بعد حفظ المشروع
            if user_id in self.manager.waiting_for_main_file:
                del self.manager.waiting_for_main_file[user_id]
            
            keyboard = [
                [InlineKeyboardButton("▶️ تشغيل الآن", callback_data=f'run_{project_dir}')],
                [InlineKeyboardButton("⏳ تشغيل لاحقاً", callback_data=f'runlater_{project_dir}')]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            status_msg = "✅ تم تعيين الملفات الرئيسية وتثبيت المكتبات بنجاح" if install_success else "⚠️ تم تعيين الملفات الرئيسية ولكن حدثت مشكلة في تثبيت بعض المكتبات"
            duration_msg = f"⏳ مدة التشغيل: {days} أيام" if days > 0 else "⏳ بدون مدة محددة"
            
            bot.edit_message_text(
                f"{status_msg}\n{duration_msg}\n\n"
                f"📦 مشروع: {os.path.basename(project_dir)} جاهز للتشغيل\n"
                f"📄 الملفات الرئيسية: {', '.join(os.path.basename(f) for f in main_files)}",
                chat_id=chat_id,
                message_id=call.message.message_id,
                reply_markup=reply_markup
            )

        @bot.callback_query_handler(func=lambda call: True)
        @error_handler
        def handle_callbacks(call):
            try:
                if call.data.startswith('run_'):
                    project_dir = call.data.split('_', 1)[1]
                    chat_id = call.message.chat.id
                    
                    bot.edit_message_text(
                        "⏳ جاري تشغيل المشروع...",
                        chat_id=chat_id,
                        message_id=call.message.message_id
                    )
                    
                    success = self.manager.run_project(project_dir, chat_id)
                    
                    if not success:
                        bot.send_message(
                            chat_id,
                            "❌ فشل بدء التشغيل، راجع السجلات"
                        )
                
                elif call.data.startswith('stop_'):
                    project_dir = call.data.split('_', 1)[1]
                    chat_id = call.message.chat.id
                    
                    bot.edit_message_text(
                        "⏳ جاري إيقاف المشروع...",
                        chat_id=chat_id,
                        message_id=call.message.message_id
                    )
                    
                    success = self.manager.stop_project(project_dir, chat_id)
                    
                    if success:
                        bot.edit_message_text(
                            f"⏹️ تم إيقاف المشروع: {os.path.basename(project_dir)}",
                            chat_id=chat_id,
                            message_id=call.message.message_id
                        )
                        logger.info(f"تم إيقاف المشروع: {project_dir}")
                    else:
                        bot.edit_message_text(
                            f"❌ فشل إيقاف المشروع: {os.path.basename(project_dir)}",
                            chat_id=chat_id,
                            message_id=call.message.message_id
                        )
                
                elif call.data.startswith('pause_'):
                    project_dir = call.data.split('_', 1)[1]
                    chat_id = call.message.chat.id
                    
                    bot.edit_message_text(
                        "⏳ جاري إيقاف المشروع مؤقتاً...",
                        chat_id=chat_id,
                        message_id=call.message.message_id
                    )
                    
                    success = self.manager.stop_project(project_dir, chat_id, pause=True)
                    
                    if success:
                        bot.edit_message_text(
                            f"⏸️ تم إيقاف المشروع مؤقتاً: {os.path.basename(project_dir)}",
                            chat_id=chat_id,
                            message_id=call.message.message_id
                        )
                        logger.info(f"تم إيقاف المشروع مؤقتاً: {project_dir}")
                    else:
                        bot.edit_message_text(
                            f"❌ فشل إيقاف المشروع مؤقتاً: {os.path.basename(project_dir)}",
                            chat_id=chat_id,
                            message_id=call.message.message_id
                        )
                
                elif call.data.startswith('resume_'):
                    project_dir = call.data.split('_', 1)[1]
                    chat_id = call.message.chat.id
                    
                    bot.edit_message_text(
                        "⏳ جاري استئناف المشروع...",
                        chat_id=chat_id,
                        message_id=call.message.message_id
                    )
                    
                    if project_dir in self.manager.paused_processes:
                        process_info = self.manager.paused_processes[project_dir]
                        success = self.manager.run_project(
                            project_dir,
                            process_info['chat_id'],
                            (process_info['end_time'] - datetime.now()).days if process_info.get('end_time') else None
                        )
                        
                        if success:
                            bot.edit_message_text(
                                f"▶️ تم استئناف المشروع: {os.path.basename(project_dir)}",
                                chat_id=chat_id,
                                message_id=call.message.message_id
                            )
                            logger.info(f"تم استئناف المشروع: {project_dir}")
                        else:
                            bot.edit_message_text(
                                f"❌ فشل استئناف المشروع: {os.path.basename(project_dir)}",
                                chat_id=chat_id,
                                message_id=call.message.message_id
                            )
                    else:
                        bot.edit_message_text(
                            "⚠️ لا يوجد مشروع متوقف مؤقتاً بهذا الاسم",
                            chat_id=chat_id,
                            message_id=call.message.message_id
                        )
                
                elif call.data.startswith('restart_'):
                    project_dir = call.data.split('_', 1)[1]
                    chat_id = call.message.chat.id
                    
                    bot.edit_message_text(
                        "⏳ جاري إعادة تشغيل المشروع...",
                        chat_id=chat_id,
                        message_id=call.message.message_id
                    )
                    
                    # إيقاف المشروع أولاً إذا كان يعمل
                    if project_dir in self.manager.running_processes:
                        self.manager.stop_project(project_dir, chat_id)
                    
                    # تشغيل المشروع مجدداً
                    success = self.manager.run_project(project_dir, chat_id)
                    
                    if success:
                        bot.edit_message_text(
                            f"🔄 تم إعادة تشغيل المشروع: {os.path.basename(project_dir)}",
                            chat_id=chat_id,
                            message_id=call.message.message_id
                        )
                        logger.info(f"تم إعادة تشغيل المشروع: {project_dir}")
                    else:
                        bot.edit_message_text(
                            f"❌ فشل إعادة تشغيل المشروع: {os.path.basename(project_dir)}",
                            chat_id=chat_id,
                            message_id=call.message.message_id
                        )
                
                elif call.data.startswith('duration_set_'):
                    parts = call.data.split('_')
                    days = int(parts[2])
                    project_dir = '_'.join(parts[3:])
                    chat_id = call.message.chat.id
                    
                    if project_dir in self.manager.running_processes:
                        if days > 0:
                            self.manager.running_processes[project_dir]['end_time'] = datetime.now() + timedelta(days=days)
                            msg = f"⏳ تم تعيين مدة التشغيل لـ {days} أيام للمشروع: {os.path.basename(project_dir)}"
                        else:
                            self.manager.running_processes[project_dir]['end_time'] = None
                            msg = f"⏳ تم إزالة مدة التشغيل للمشروع: {os.path.basename(project_dir)}"
                        
                        bot.edit_message_text(
                            msg,
                            chat_id=chat_id,
                            message_id=call.message.message_id
                        )
                        logger.info(msg)
                    else:
                        bot.edit_message_text(
                            f"⚠️ لا يمكن تعيين المدة لمشروع غير نشط",
                            chat_id=chat_id,
                            message_id=call.message.message_id
                        )
                
                elif call.data.startswith('delete_'):
                    project_dir = call.data.split('_', 1)[1]
                    chat_id = call.message.chat.id
                    user_id = self.manager.get_user_id_by_chat_id(chat_id)
                    
                    if user_id and user_id in self.manager.user_projects:
                        self.manager.user_projects[user_id] = [p for p in self.manager.user_projects[user_id] if p['project_dir'] != project_dir]
                        self.manager.save_user_projects()
                    
                    if project_dir in self.manager.running_processes:
                        self.manager.stop_project(project_dir, chat_id)
                    
                    if project_dir in self.manager.paused_processes:
                        del self.manager.paused_processes[project_dir]
                    
                    try:
                        shutil.rmtree(project_dir)
                        bot.edit_message_text(
                            f"🗑️ تم حذف المشروع: {os.path.basename(project_dir)}",
                            chat_id=chat_id,
                            message_id=call.message.message_id
                        )
                        logger.info(f"تم حذف المشروع: {project_dir}")
                    except Exception as e:
                        bot.edit_message_text(
                            f"❌ فشل حذف المشروع: {str(e)}",
                            chat_id=chat_id,
                            message_id=call.message.message_id
                        )
                        logger.error(f"فشل حذف المشروع: {str(e)}")
                
                elif call.data == 'cancel_selection':
                    bot.delete_message(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id
                    )
                    
            except Exception as e:
                logger.error(f"خطأ في معالجة الاستدعاء: {str(e)}\n{traceback.format_exc()}")
                bot.answer_callback_query(call.id, "❌ حدث خطأ أثناء معالجة طلبك")

    @error_handler
    def run(self):
        logger.info("جاري تشغيل البوت...")
        while True:
            try:
                bot.polling(none_stop=True, timeout=60)
            except Exception as e:
                logger.error(f"حدث خطأ في تشغيل البوت: {str(e)}\n{traceback.format_exc()}")
                logger.info("إعادة المحاولة بعد 10 ثواني...")
                time.sleep(10)

if __name__ == '__main__':
    try:
        logger.info("بدء تشغيل البوت...")
        bot_instance = PythonHostingBot()
        
        try:
            bot_instance.run()
        except KeyboardInterrupt:
            logger.info("استلام إشارة إيقاف البوت...")
            bot_instance.manager.cleanup()
            sys.exit(0)
            
    except Exception as e:
        logger.error(f"حدث خطأ فادح: {str(e)}\n{traceback.format_exc()}")
        sys.exit(1)