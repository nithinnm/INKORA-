"""Per-kiosk pricing; money is represented exclusively in integer paise."""
from decimal import Decimal,InvalidOperation
from flask import abort,redirect,render_template,request,url_for
from sqlalchemy import select
from . import db,Kiosk,Audit

DEFAULT_PRICES={'bw_single':250,'bw_duplex':300,'color_single':1000,'color_duplex':1600,'art_single':4000,'art_duplex':7000}


def register_pricing(app,login_required):
    @app.route('/kiosks/<int:kiosk_id>/settings',methods=['GET','POST'])
    @login_required(admin=True)
    def kiosk_settings(user,kiosk_id):
        kiosk=db.session.scalar(select(Kiosk).where(Kiosk.id==kiosk_id).with_for_update(of=Kiosk))
        if not kiosk:
            abort(404)
        if request.method=='POST':
            prices={}
            for name in DEFAULT_PRICES:
                try:
                    price=Decimal(request.form.get(name,''))
                    if not price.is_finite() or price<Decimal('0.25') or price>Decimal('1000') or price*100!=(price*100).to_integral_value():
                        raise ValueError()
                    prices[name]=int(price*100)
                except (InvalidOperation,ValueError):
                    abort(400,'Prices must be ₹0.25–₹1,000 with no more than two decimal places.')
            kiosk.prices=prices;kiosk.configuration_version+=1
            db.session.add(Audit(actor_id=user.id,action='pricing_updated',target_id=kiosk.id))
            db.session.commit()
            return redirect(url_for('dashboard'))
        return render_template('settings.html',kiosk=kiosk)
