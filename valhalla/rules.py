"""Versioned purchase rules shared by public page, checkout and acceptance evidence."""
import hashlib
import json

RULES = [
('Recepción de consultantes', 'El arrendatario debe recibir a su consultante personalmente. Puede reunirse con su cliente en el hall del edificio y subir juntos o citarlo unos minutos después de la hora reservada para asegurar su recepción.'),
('Compañía', 'Los usuarios deben venir sin compañía para evitar la presencia de personas ajenas a la consulta y prevenir robos. Se exceptúan consultantes que requieran asistencia, como menores de edad o personas con discapacidad, quienes pueden venir con un cuidador o acompañante autorizado.'),
('Alimentos y ambiente', 'Está prohibido comer (pan, almuerzo, galletas, etc.) o perturbar el entorno con ruidos molestos u olores extraños, como inciensos. Se permite consumir café o té y otras bebidas en recipientes cerrados.'),
('Responsabilidad del espacio', 'Los arrendatarios deben dejar la sala en las mismas condiciones en que la recibieron: limpia, sin basura y con el mobiliario en su lugar. En caso de daño accidental a equipos o mobiliario, deben reportarlo de inmediato y hacerse responsables de su reposición o reparación.'),
('Duración de la hora', 'Cada hora reservada dura 55 minutos, con un máximo de 5 minutos adicionales para desocupar el espacio en caso de emergencias, completando un máximo de 60 minutos. Superado ese tiempo, se aplicará un cobro proporcional de la siguiente hora.'),
('Modificación de horas', 'Cada hora reservada puede modificarse una sola vez, con al menos 2 horas de anticipación. No se permiten anulaciones ni reintegros de horas agendadas por el arrendatario.'),
('Uso personal', 'Los planes de horas son de uso personal e intransferible: solo el arrendatario titular puede utilizar las horas contratadas. No se permite compartirlas con otros profesionales ni cederlas a terceros, ya que cada reserva está vinculada a un usuario específico.'),
('Acceso a salas', 'El ingreso a las salas solo está permitido en el horario reservado. No se pueden usar como salas de espera para arrendatarios ni consultantes. En caso de llegar antes de la hora, el consultante deberá esperar fuera de la sala.'),
('Materiales de oficina', 'Los arrendatarios no pueden dejar materiales en la oficina. En caso de hacerlo, Valhalla Office no se hace responsable por pérdidas.'),
('Cumplimiento de normas', 'El incumplimiento puede derivar en advertencias, sanciones o la suspensión de la membresía sin reembolso. Los casos graves o reincidentes pueden implicar la eliminación permanente del acceso a Valhalla Office, sin perjuicio de los derechos que correspondan conforme a la normativa aplicable.'),
('Horarios de atención', 'El horario de atención por WhatsApp es de lunes a viernes de 8:30 a 18:30 horas. Para emergencias, contactar a valhallagartenoffice@gmail.com.'),
('Número de personas por sala', 'Cada sala está diseñada para un profesional y su consultante. En casos especiales se permite el ingreso de una pareja, cuidadores y/o un menor si la consulta lo requiere. No se permite el ingreso de más personas.')]
RULES_TEXT = json.dumps(RULES, ensure_ascii=False, separators=(',',':'))
RULES_VERSION = '2026-10-19-' + hashlib.sha256(RULES_TEXT.encode()).hexdigest()[:16]


def purchase_rules():
    return {'version':RULES_VERSION,'items':[{'title':title,'text':text} for title,text in RULES]}


def validate_acceptance(acceptance):
    return isinstance(acceptance,dict) and acceptance.get('accepted') is True and acceptance.get('version') == RULES_VERSION
