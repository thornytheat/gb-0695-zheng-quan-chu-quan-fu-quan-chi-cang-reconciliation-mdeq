from flask import Blueprint, jsonify
from app.api import stage
bp=Blueprint('m5',__name__)
@bp.route('/api/m5', methods=['GET','POST'])
def module_5(): return jsonify(stage(5))
